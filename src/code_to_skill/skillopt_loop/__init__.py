"""SkillOpt 优化循环（模块 4）。

主训练循环：rollout → reflect → aggregate → select → update → evaluate
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import Any

from .types import (
    EditOp,
    RankedEdit,
)
from .skill_ops import apply_edits
from .gate import GateManager
from .scheduler import EditBudgetScheduler
from .step_buffer import StepBufferManager

from .scoring import score_rollout_result

logger = logging.getLogger(__name__)


def _gate_icon(action: str) -> str:
    """门禁动作图标。"""
    return {"accept_new_best": "⭐", "accept": "✓", "reject": "✗"}.get(action, "?")


def compute_semantic_hash(content: str) -> str:
    """计算语义 hash（空白归一化后 SHA256）。"""
    normalized = re.sub(r"\s+", " ", content).strip()
    return hashlib.sha256(normalized.encode()).hexdigest()[:12]


# ── State Manager ────────────────────────────────────────────

from .resume_state import (
    save_runtime_state,
    load_skills_for_resume,
    load_history,
    resume_offsets,
    load_runtime_state,
)

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
    rollout_backend_id: str | None = None,
    optimizer_backend_id: str | None = None,
    model_provider: dict | None = None,
    budget_strategy: str = "constant",
    patience: int = 10,
    gate_metric: str = "hard",
    accumulation: int = 1,
    enable_slow_update: bool = False,
    enable_meta_skill: bool = False,
    slow_update_gate: bool = True,
    test_split_ratio: float = 0.0,
    selection_items: list[dict] | None = None,
    test_items: list[dict] | None = None,
    adapter: Any = None,
    token_budgets: dict | None = None,
    code_repos: list[dict] | None = None,
    graph_db_path: str = "",
    repo_root: str = "",
    graph_sources: list[dict] | None = None,
    enable_code_tools: bool = True,
    max_tool_rounds: int = 5,
    rollout_max_tool_rounds: int = 2,
    resume: bool = False,
    pipeline_settings: Any = None,
    run_root: str = "",
    graph_role_hints: dict | None = None,
    reflect_prompts: dict | None = None,
    skillopt_settings: dict | None = None,
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

    from .token_budgets import configure_token_budgets, get_token_budgets
    configure_token_budgets(token_budgets)
    logger.info("[M4] token budgets: rollout=%d reflect_failure=%d reflect_retry=%s",
                get_token_budgets().rollout,
                get_token_budgets().reflect_failure,
                get_token_budgets().reflect_retry)

    # ── Config（在 split 解析后写入，见下方 _save_config）────────

    # ── Backend 初始化 ─────────────────────────────────────
    from .separation import BackendManager, Accumulator
    backend_mgr = BackendManager.from_skillopt(
        use_llm_rollout=use_llm_rollout,
        use_llm_optimizer=True,
        rollout_backend_id=rollout_backend_id,
        optimizer_backend_id=optimizer_backend_id,
        model_provider=model_provider,
    )
    if use_llm_rollout and backend_mgr.target is None:
        logger.warning("[M4] use_llm_rollout=true 但 rollout backend 不可用，将降级为规则 rollout")

    # ── Adapter 初始化 ─────────────────────────────────────
    from .envs import DEFAULTAdapter
    if adapter is None:
        adapter = DEFAULTAdapter(
            use_llm=use_llm_rollout,
            code_repos=code_repos,
            enable_code_tools=enable_code_tools,
            max_tool_rounds=max_tool_rounds,
            rollout_max_tool_rounds=rollout_max_tool_rounds,
        )
    from .separation import BackendManager, resolve_judge_backend_id
    judge_id = resolve_judge_backend_id(skillopt_settings, model_provider)
    judge_backend = BackendManager._try_create_backend(judge_id) if judge_id else None
    if judge_id and judge_backend:
        logger.info("[M4] judge backend: %s", judge_id)

    adapter.setup({
        "code_repos": code_repos,
        "graph_db_path": graph_db_path,
        "repo_root": repo_root or (code_repos[0]["path"] if code_repos else ""),
        "graph_sources": graph_sources,
        "enable_code_tools": enable_code_tools,
        "max_tool_rounds": max_tool_rounds,
        "rollout_max_tool_rounds": rollout_max_tool_rounds,
        "reflect_prompts": reflect_prompts or {},
        "judge_backend": judge_backend,
    })
    code_tools = getattr(adapter, "code_tools", None)
    if code_tools and getattr(code_tools, "enabled", False):
        logger.info(
            "[M4] code tools: repos=%d graph=%s reflect_tools=%d rollout_tools=%d",
            len(code_repos or []),
            "yes" if getattr(code_tools, "graph_enabled", False) else "no",
            max_tool_rounds,
            rollout_max_tool_rounds,
        )

    # ── Split: train / selection / test ─────────────────────
    from .benchmark_splits import BenchmarkSplits
    splits = BenchmarkSplits(
        train=benchmark_items,
        selection=selection_items or [],
        test=test_items or [],
    )
    resolved = splits.resolve(
        selection_split_ratio=selection_split_ratio,
        test_split_ratio=test_split_ratio,
    )
    train_items = resolved.train
    selection_items = resolved.selection
    test_items = resolved.test
    splits.log_validation()

    from code_to_skill.cli.pipeline_config import (
        PipelineSettings,
        build_artifact_contract,
        discover_pipeline_artifacts,
        write_artifact_contract,
    )
    from .code_evidence import validate_context_refs_for_items

    pipe = (
        pipeline_settings
        if isinstance(pipeline_settings, PipelineSettings)
        else PipelineSettings(**pipeline_settings) if isinstance(pipeline_settings, dict)
        else PipelineSettings()
    )
    effective_run_root = run_root or os.path.dirname(output_dir)
    repo_specs: list[dict] = []
    if graph_sources:
        for gs in graph_sources:
            db_path = gs.get("db_path", "")
            code_root = os.path.dirname(db_path) if db_path else ""
            ref = os.path.basename(code_root) if code_root else "HEAD"
            repo_id = gs.get("repo_id") or (
                os.path.basename(os.path.dirname(code_root)) if code_root else "default"
            )
            repo_specs.append({
                "id": repo_id,
                "ref": ref,
                "path": gs.get("repo_root", repo_root or ""),
            })

    artifacts = discover_pipeline_artifacts(
        effective_run_root, repos=repo_specs or None,
    )
    if pipe.write_artifact_contract:
        contract = build_artifact_contract(
            artifacts,
            pipeline_settings=pipe,
            extra={
                "graph_db_path": graph_db_path,
                "train_items": len(train_items),
                "selection_items": len(selection_items),
                "test_items": len(test_items),
            },
        )
        contract_path = write_artifact_contract(output_dir, contract)
        logger.info("[M4] artifact contract → %s", contract_path)

    if pipe.validate_context_refs:
        all_items = train_items + selection_items + test_items
        ref_report = validate_context_refs_for_items(
            all_items, code_tools, repo_root=repo_root or "",
        )
        ref_path = os.path.join(output_dir, "context_ref_report.json")
        with open(ref_path, "w", encoding="utf-8") as f:
            json.dump(ref_report, f, indent=2, ensure_ascii=False)
        summary = ref_report.get("summary", {})
        logger.info(
            "[M4] context refs: %d/%d resolved (%.0f%%)",
            summary.get("resolved", 0),
            summary.get("total_refs", 0),
            100 * summary.get("resolve_rate", 0),
        )

    from .graph_sidecars import GraphSidecarContext
    graph_sidecars = GraphSidecarContext.from_artifacts(
        artifacts, pipe, graph_role_hints=graph_role_hints,
    )
    adapter.graph_sidecars = graph_sidecars
    sidecar_flags = []
    if graph_sidecars.entrypoints:
        sidecar_flags.append("entrypoints")
    if graph_sidecars.role_index:
        sidecar_flags.append("role_index")
    if graph_sidecars.evidence_index:
        sidecar_flags.append("evidence_index")
    if sidecar_flags:
        logger.info("[M4] graph sidecars loaded: %s", ", ".join(sidecar_flags))

    if resolved.use_explicit_splits:
        logger.info("[M4] 使用显式 benchmark split: train=%d selection=%d test=%d",
                     len(train_items), len(selection_items), len(test_items))
    else:
        logger.info("[M4] 使用 ratio split: train=%d selection=%d test=%d (ratio=%.2f/%.2f)",
                     len(train_items), len(selection_items), len(test_items),
                     selection_split_ratio, test_split_ratio)

    _save_config(output_dir, {
        "num_epochs": num_epochs, "batch_size": batch_size,
        "edit_budget": edit_budget, "selection_split_ratio": selection_split_ratio,
        "use_llm_rollout": use_llm_rollout,
        "rollout_backend": rollout_backend_id,
        "optimizer_backend": optimizer_backend_id,
        "budget_strategy": budget_strategy,
        "patience": patience, "gate_metric": gate_metric,
        "accumulation": accumulation, "enable_slow_update": enable_slow_update,
        "enable_meta_skill": enable_meta_skill,
        "slow_update_gate": slow_update_gate,
        "test_split_ratio": test_split_ratio,
        "use_explicit_splits": resolved.use_explicit_splits,
        "split_source": resolved.source,
        "initial_skill_chars": len(initial_skill),
        "train_items": len(train_items),
        "selection_items": len(selection_items),
        "test_items": len(test_items),
        "enable_code_tools": enable_code_tools,
        "max_tool_rounds": max_tool_rounds,
        "rollout_max_tool_rounds": rollout_max_tool_rounds,
        "code_repos": len(code_repos or []),
    })

    # selection 较小时自动用 soft gate
    effective_gate_metric = gate_metric
    n_sel = len(selection_items)
    if n_sel < 5:
        effective_gate_metric = "soft"
        if gate_metric != "soft":
            logger.info("[M4] selection=%d < 5，gate_metric %s → soft", n_sel, gate_metric)
    elif n_sel < 20 and gate_metric == "hard":
        effective_gate_metric = "mixed"
        logger.info("[M4] selection=%d < 20，gate_metric hard → mixed", n_sel)

    current_skill = initial_skill
    prev_epoch_skill = initial_skill
    best_skill = initial_skill
    current_hard = 0.0
    current_soft = 0.0
    current_score = 0.0
    best_score = 0.0
    best_step = 0
    history: list[dict] = []
    start_epoch, start_batch_start, step_counter = 0, 0, 0
    last_rollout_avg = 0.0

    if resume and os.path.isdir(output_dir):
        loaded_current, loaded_best, last_step = load_skills_for_resume(output_dir, initial_skill)
        current_skill = loaded_current
        best_skill = loaded_best
        prev_epoch_skill = loaded_current
        history = load_history(output_dir)
        start_epoch, start_batch_start, step_counter = resume_offsets(output_dir)
        state = load_runtime_state(output_dir)
        if state:
            best_score = float(state.get("best_score", 0.0))
            best_step = int(state.get("best_step", 0))
            current_score = float(state.get("current_score", best_score))
            if history:
                last_rollout_avg = float(history[-1].get("rollout_score", 0.0))
            logger.info(
                "[M4] 断点续训: step=%d epoch=%d batch_start=%d best_score=%.3f",
                last_step, start_epoch, start_batch_start, best_score,
            )

    # ── 组件初始化 ─────────────────────────────────────────
    total_steps = num_epochs * max(1, len(train_items) // max(1, batch_size))
    from .gate import select_gate_score
    gate = GateManager(patience=patience, metric=effective_gate_metric)  # type: ignore[arg-type]
    scheduler = EditBudgetScheduler(
        initial_budget=edit_budget, min_budget=1,
        total_steps=total_steps, strategy=budget_strategy,
    )
    buffer = StepBufferManager()
    from .cache import SelectionCache
    cache = SelectionCache(
        cache_path=os.path.join(output_dir, "cache", "selection_scores.json")
    )
    if resume:
        cache.load()
    from .meta_skill import MetaSkill
    from .training_curve import TrainingCurveRecorder
    meta_skill = MetaSkill()
    curve = TrainingCurveRecorder(
        output_dir, gate_metric=effective_gate_metric, resume=resume,
    )
    acc = Accumulator(accumulate=accumulation)

    # ── Initial evaluation ──────────────────────────────────
    logger.info("[M4] 开始训练: skill=%d chars, train=%d items, selection=%d items, epochs=%d, batch=%d acc=%d",
                len(initial_skill), len(train_items), len(selection_items),
                num_epochs, batch_size, accumulation)
    if selection_items and not resume:
        eval_result = adapter.evaluate(current_skill, selection_items, target_backend=backend_mgr.target)
        current_hard = eval_result["accuracy"]
        current_soft = eval_result["soft"]
        init_decision = gate.evaluate(current_hard, current_soft, 0.0, 0.0)
        current_score = init_decision.candidate_score
        best_score = init_decision.candidate_score

        logger.info("[M4] 初始评分: hard=%.3f soft=%.3f acc=%.3f f1=%.3f (gate=%.3f [%s])",
                     current_hard, current_soft, eval_result["accuracy"], eval_result["f1"],
                     best_score, gate.metric)
        curve.record_init(
            selection_hard=current_hard,
            selection_soft=current_soft,
            selection_gate=best_score,
        )
    elif resume:
        logger.info("[M4] 跳过初始 eval（断点续训）")

    for epoch in range(start_epoch, num_epochs):
        logger.info("[M4] === Epoch %d/%d ===", epoch + 1, num_epochs)
        batch_begin = start_batch_start if epoch == start_epoch else 0
        for batch_start in range(batch_begin, len(train_items), batch_size):
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
            reflect_result = reflect_llm(
                all_results, current_skill,
                rejected_edits=rejected_edits,
                step_buffer=step_buffer,
                meta_skill_context=meta_skill.render() if enable_meta_skill else "",
                backend=backend_mgr.optimizer,
                code_tools=code_tools,
                max_tool_rounds=max_tool_rounds,
                graph_sidecars=graph_sidecars,
                adapter=adapter,
            )
            patches = reflect_result.patches
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

            # 3.5 Validate — 过滤占位/低质量编辑
            from .edit_validator import filter_valid_edits
            valid_edits, rejected_validations = filter_valid_edits(merged.edits, current_skill)
            for edit, reason in rejected_validations:
                logger.info("[M4] validate: REJECT [%s] %s — %s", edit.op, edit.content[:40], reason)
                buffer.record_rejected_edit(step_counter, edit)
            scenario_rules_triggered = 0
            if not valid_edits:
                from .scenario_rules import build_scenario_edits

                scenario_edits = build_scenario_edits(all_results, current_skill)
                if scenario_edits:
                    scenario_rules_triggered = len(scenario_edits)
                    valid_edits, scenario_rejected = filter_valid_edits(
                        scenario_edits, current_skill,
                    )
                    rejected_validations.extend(scenario_rejected)
                    if valid_edits:
                        logger.info(
                            "[M4] validate: 场景规则兜底 %d 条",
                            len(valid_edits),
                        )
                if not valid_edits:
                    logger.info("[M4] validate: 无有效编辑，跳过本 step")
                    curve.record_skip(
                        step=step_counter,
                        epoch=epoch + 1,
                        reason="no_valid_edits",
                        rollout_results=all_results,
                        patch_count=len(patches),
                    )
                    from .edit_traceability import edit_to_dict

                    _save_step_artifacts(
                        output_dir, step_counter,
                        ranked=[], edit_reports=[],
                        prev_skill=current_skill, candidate_skill=current_skill,
                        candidate_hard=0.0, candidate_soft=0.0, action="skip_validate",
                        rollout_results=all_results, patches=patches,
                        rejected_edits=[edit_to_dict(e) for e, _ in rejected_validations],
                    )
                    continue

            _save_step_metrics(
                output_dir, step_counter,
                evidence_metrics=reflect_result.evidence_metrics,
                reflect_tool_rounds_max=reflect_result.reflect_tool_rounds_max,
                rollout_passed=passed,
                rollout_failed=failed,
                patch_count=len(patches),
                custom_reflect_prompt=getattr(reflect_result, "custom_reflect_prompt", False),
                scenario_rules_triggered=scenario_rules_triggered,
            )

            # 4. Select（LLM 排序 + budget 截断）
            from .llm_components import select_edits_llm
            step_budget = scheduler.step()
            ranked_dicts = select_edits_llm(
                valid_edits, current_skill, step_budget,
                backend=backend_mgr.optimizer,
                rollout_results=all_results,
            )
            ranked = [RankedEdit(**r) if isinstance(r, dict) else r for r in ranked_dicts]
            for i, r in enumerate(ranked):
                if buffer.is_edit_redundant(r.edit):
                    logger.info("[M4] select #%d: SKIP (redundant) [%s] %s", i + 1, r.edit.op, r.edit.content[:40])
                    continue
                logger.info("[M4] select #%d: [%s] %s", i + 1, r.edit.op, r.edit.content[:60])

            # 5. Update
            candidate_content, edit_reports = apply_edits(current_skill, [e.edit for e in ranked])
            candidate_hash = compute_semantic_hash(candidate_content)
            size_delta = len(candidate_content) - len(current_skill)
            logger.info("[M4] update: hash=%s delta=%+d chars", candidate_hash[:8], size_delta)

            # 6. Evaluate（selection cache）
            cached = cache.get(candidate_hash)
            if cached is not None:
                candidate_hard = cached.get("hard", cached["gate_score"])
                candidate_soft = cached.get("soft", cached["gate_score"])
                candidate_gate = cached["gate_score"]
                logger.info("[M4] evaluate: CACHED hard=%.3f soft=%.3f (epoch=%d step=%d)",
                             candidate_hard, candidate_soft, cached.get("epoch", 0), cached.get("step", 0))
            else:
                eval_result = adapter.evaluate(candidate_content, selection_items, target_backend=backend_mgr.target)
                candidate_hard = eval_result.get("accuracy", 0.0)
                candidate_soft = eval_result["soft"]
                candidate_gate = select_gate_score(candidate_hard, candidate_soft,
                                                    metric=gate.metric, mixed_weight=gate.mixed_weight)
                cache.put(candidate_hash, candidate_hard,
                          candidate_soft, candidate_gate, epoch + 1, step_counter)
                logger.info("[M4] evaluate: hard=%.3f soft=%.3f acc=%.3f f1=%.3f (gate=%.3f, best=%.3f [%s])",
                             candidate_hard, candidate_soft, eval_result.get("accuracy", 0.0),
                             eval_result.get("f1", 0.0), candidate_gate, best_score, gate.metric)

            # Gate — uses the gate metric projection
            decision = gate.evaluate(
                candidate_hard, candidate_soft, best_score, current_score,
                train_rollout=rollout_avg,
                prev_train_rollout=last_rollout_avg,
            )
            last_rollout_avg = rollout_avg
            action = decision.action
            candidate_gate = decision.candidate_score  # the projected score
            logger.info("[M4] gate: %s reason=%s", _gate_icon(action), decision.reason)

            if action == "accept_new_best":
                best_score = candidate_gate
                best_skill = candidate_content
                best_step = step_counter
                for r in ranked:
                    buffer.record_accepted_edit(step_counter, r.edit)
            elif action == "accept":
                if candidate_gate >= best_score - 1e-9:
                    best_score = candidate_gate
                    best_skill = candidate_content
                    best_step = step_counter
                for r in ranked:
                    buffer.record_accepted_edit(step_counter, r.edit)
            else:
                for r in ranked:
                    buffer.record_rejected_edit(step_counter, r.edit)

            current_hard = candidate_hard if action != "reject" else current_hard
            current_soft = candidate_soft if action != "reject" else current_soft
            current_score = candidate_gate if action != "reject" else current_score
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
                "selection_score": round(candidate_gate, 3),
                "gate_action": action,
                "best_score": round(best_score, 3),
                "edit_count": len(ranked),
            }
            history.append(record)
            curve.record_gate(
                step=step_counter,
                epoch=epoch + 1,
                rollout_results=all_results,
                selection_hard=candidate_hard,
                selection_soft=candidate_soft,
                selection_gate=candidate_gate,
                best_score=best_score,
                current_score=current_score,
                gate_action=action,
                gate_reason=decision.reason,
                edit_count=len(ranked),
                patch_count=len(patches),
            )

            # ── Step artifacts ─────────────────────────────
            _save_step_artifacts(
                output_dir, step_counter, ranked, edit_reports,
                current_skill, candidate_content, candidate_hard,
                candidate_soft, action,
                rollout_results=all_results, patches=patches,
            )

            # ── Step checkpoint ────────────────────────────
            from .test_eval import StepCheckpoint
            ckpt = StepCheckpoint(
                step=step_counter, phase="evaluate",
                rollout_completed=len(all_results), rollout_total=len(all_results),
                last_minibatch_completed=1,
            )
            ckpt.save(os.path.join(output_dir, "step_checkpoint.json"))

            skill_snap = os.path.join(output_dir, "skills", f"skill_v{step_counter:04d}.md")
            save_runtime_state(
                output_dir, step_counter, current_score, best_score, best_step,
                current_skill_path=skill_snap if os.path.isfile(skill_snap) else "",
                best_skill_path=os.path.join(output_dir, "best_skill.md"),
                epoch=epoch,
                next_batch_start=batch_start + batch_size,
                step_internal=ckpt.to_dict(),
            )
            with open(os.path.join(output_dir, "history.json"), "w", encoding="utf-8") as f:
                json.dump(history, f, indent=2, ensure_ascii=False)
            cache.save()

            if gate.should_early_stop:
                break

        # ── Epoch End ───────────────────────────────────────
        # Flush remaining accumulated results
        remaining = acc.flush_remaining()
        if remaining:
            logger.info("[M4] Flushed %d remaining accumulated results at epoch end", len(remaining))

        epoch_slow_gate: str | None = None
        epoch_slow_reason: str | None = None
        epoch_comparison: dict | None = None
        slow_result: dict = {}

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
                target_backend=backend_mgr.target,
                optimizer_backend=backend_mgr.optimizer,
            )
            if slow_result["slow_update_content"]:
                candidate_slow = apply_slow_update(
                    current_skill, slow_result["slow_update_content"],
                )
                apply_slow = True
                slow_action = "force_apply"
                if slow_update_gate and selection_items:
                    slow_hash = compute_semantic_hash(candidate_slow)
                    cached_slow = cache.get(slow_hash)
                    if cached_slow is not None:
                        slow_hard = cached_slow.get("hard", cached_slow["gate_score"])
                        slow_soft = cached_slow.get("soft", cached_slow["gate_score"])
                        slow_gate = cached_slow["gate_score"]
                    else:
                        slow_eval = adapter.evaluate(
                            candidate_slow, selection_items, target_backend=backend_mgr.target,
                        )
                        slow_hard = slow_eval.get("accuracy", 0.0)
                        slow_soft = slow_eval["soft"]
                        slow_gate = select_gate_score(
                            slow_hard, slow_soft, metric=gate.metric, mixed_weight=gate.mixed_weight,
                        )
                        cache.put(slow_hash, slow_hard, slow_soft, slow_gate, epoch + 1, step_counter)
                    slow_decision = gate.evaluate(
                        slow_hard, slow_soft, best_score, current_score,
                    )
                    slow_action = slow_decision.action
                    apply_slow = slow_action != "reject"
                    epoch_slow_gate = slow_action
                    epoch_slow_reason = slow_decision.reason
                    logger.info(
                        "[M4] slow update gate: %s (%s)",
                        _gate_icon(slow_action), slow_decision.reason,
                    )
                    if apply_slow:
                        if slow_action == "accept_new_best":
                            best_score = slow_decision.candidate_score
                            best_skill = candidate_slow
                            best_step = step_counter
                        current_score = slow_decision.candidate_score
                        current_hard = slow_hard
                        current_soft = slow_soft
                if apply_slow:
                    current_skill = candidate_slow
                    if not slow_update_gate or not selection_items:
                        best_skill = apply_slow_update(
                            best_skill, slow_result["slow_update_content"],
                        )
                    elif slow_action == "accept_new_best":
                        pass  # best_skill already set above
                    _save_slow_update_artifacts(
                        output_dir, epoch + 1, {**slow_result, "gate_action": slow_action},
                        prev_epoch_skill, current_skill,
                    )
                    logger.info(
                        "[M4] Slow update applied: %d chars (gate=%s)",
                        len(slow_result["slow_update_content"]), slow_action,
                    )
                else:
                    logger.info("[M4] Slow update rejected by selection gate")

            if slow_result.get("comparison_pairs"):
                epoch_comparison = slow_result["comparison_pairs"]

        # Meta Skill（与 slow_update 解耦：仅依赖 enable_meta_skill）
        if enable_meta_skill:
            logger.info("[M4] === Meta Skill epoch %d ===", epoch + 1)
            meta_skill.update(
                prev_skill=prev_epoch_skill,
                curr_skill=current_skill,
                accepted_edits=buffer.get_accepted_edits(),
                rejected_edits=buffer.get_rejected_edits(),
                comparison_pairs=epoch_comparison or slow_result.get("comparison_pairs", {}),
                optimizer_backend=backend_mgr.optimizer,
            )
            _save_meta_skill_artifacts(output_dir, epoch + 1, meta_skill)

        curve.record_epoch_end(
            step=step_counter,
            epoch=epoch + 1,
            best_score=best_score,
            current_score=current_score,
            slow_update_gate=epoch_slow_gate,
            slow_update_reason=epoch_slow_reason,
            comparison_pairs=epoch_comparison,
        )

        # Save prev_epoch_skill for next epoch's slow update
        prev_epoch_skill = current_skill

        # Epoch-level early stop
        if gate.should_early_stop:
            logger.info("[M4] 早停在 epoch %d/%d", epoch + 1, num_epochs)
            break

        # Epoch end: save state with step checkpoint
        from .test_eval import StepCheckpoint as _StepCP
        epoch_ckpt = _StepCP(
            step=step_counter, phase="epoch_end",
            rollout_completed=step_counter * batch_size, rollout_total=step_counter * batch_size,
            last_minibatch_completed=1,
        )
        save_runtime_state(
            output_dir, step_counter, current_score, best_score, best_step,
            current_skill_path=os.path.join(output_dir, "skills", f"skill_v{step_counter:04d}.md"),
            best_skill_path=os.path.join(output_dir, "best_skill.md"),
            epoch=epoch + 1,
            next_batch_start=0,
            step_internal=epoch_ckpt.to_dict(),
        )
        cache.save()

    # ── Final ───────────────────────────────────────────────
    # 训练结束时：若 current_skill 在 selection 上优于 best_skill，提升导出产物
    if selection_items and current_skill.strip() != best_skill.strip():
        curr_eval = adapter.evaluate(
            current_skill, selection_items, target_backend=backend_mgr.target,
        )
        best_eval = adapter.evaluate(
            best_skill, selection_items, target_backend=backend_mgr.target,
        )
        curr_gate = select_gate_score(
            curr_eval.get("accuracy", 0.0), curr_eval["soft"],
            metric=gate.metric, mixed_weight=gate.mixed_weight,
        )
        best_gate = select_gate_score(
            best_eval.get("accuracy", 0.0), best_eval["soft"],
            metric=gate.metric, mixed_weight=gate.mixed_weight,
        )
        if curr_gate > best_gate + gate.delta or (
            curr_gate >= best_gate - 1e-9
            and current_skill.strip() != best_skill.strip()
        ):
            logger.info(
                "[M4] Finalize: export current_skill on selection "
                "(%.3f vs best %.3f)",
                curr_gate, best_gate,
            )
            best_skill = current_skill
            best_score = max(curr_gate, best_gate)

    # Test eval
    test_report = {}
    if test_items:
        from .test_eval import evaluate_test_split
        test_report = evaluate_test_split(
            best_skill,
            test_items,
            adapter=adapter,
            target_backend=backend_mgr.target,
            output_dir=os.path.join(output_dir, "final_eval"),
        )
        logger.info("[M4] Test eval: score=%.3f hard=%.3f n=%d",
                     test_report["test_score"], test_report["test_hard"], test_report["n_items"])
        curve.record_test(
            test_score=test_report["test_score"],
            test_hard=test_report["test_hard"],
            n_items=test_report["n_items"],
            best_score=best_score,
        )

    curve.finalize(best_step=best_step, test_report=test_report or None)

    if pipe.auto_plot_training_curve:
        try:
            from .training_curve import plot_training_curve
            plot_training_curve(output_dir)
            logger.info("[M4] training curve SVG written")
        except (OSError, ValueError) as exc:
            logger.warning("[M4] training curve plot skipped: %s", exc)

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


# ── Artifact helpers ──────────────────────────────────────────

def _save_step_metrics(
    output_dir: str,
    step: int,
    *,
    evidence_metrics: Any,
    reflect_tool_rounds_max: int = 0,
    rollout_passed: int = 0,
    rollout_failed: int = 0,
    patch_count: int = 0,
    custom_reflect_prompt: bool = False,
    scenario_rules_triggered: int = 0,
) -> None:
    step_dir = os.path.join(output_dir, "steps", f"step_{step:04d}")
    os.makedirs(step_dir, exist_ok=True)
    metrics = evidence_metrics.to_dict() if hasattr(evidence_metrics, "to_dict") else evidence_metrics
    payload = {
        "step": step,
        "rollout": {"passed": rollout_passed, "failed": rollout_failed},
        "reflect": {
            "patch_count": patch_count,
            "tool_rounds_max": reflect_tool_rounds_max,
            "custom_reflect_prompt": custom_reflect_prompt,
            "scenario_rules_triggered": scenario_rules_triggered,
        },
        "code_evidence": {
            **metrics,
            "ref_resolve_rate": round(
                getattr(evidence_metrics, "ref_resolve_rate", 0.0), 4,
            ),
            "evidence_hit_rate": round(
                getattr(evidence_metrics, "evidence_hit_rate", 0.0), 4,
            ),
        },
    }
    with open(os.path.join(step_dir, "metrics.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def _save_config(output_dir: str, cfg: dict) -> None:
    path = os.path.join(output_dir, "config.json")
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)
    logger.debug("Saved config.json")

def _save_step_artifacts(
    output_dir: str,
    step: int,
    ranked: list,
    edit_reports: list,
    prev_skill: str,
    candidate_skill: str,
    candidate_hard: float,
    candidate_soft: float,
    action: str,
    *,
    rollout_results: list[dict] | None = None,
    patches: list[dict] | None = None,
    rejected_edits: list[dict] | None = None,
) -> None:
    from .edit_traceability import ranked_edit_to_proposal, rollout_failure_records

    step_dir = os.path.join(output_dir, "steps", f"step_{step:04d}")
    os.makedirs(step_dir, exist_ok=True)

    if rollout_results is not None:
        with open(os.path.join(step_dir, "rollout_summary.json"), "w", encoding="utf-8") as f:
            json.dump({
                "step": step,
                "total": len(rollout_results),
                "passed": sum(1 for r in rollout_results if r.get("hard") == 1),
                "failed": sum(1 for r in rollout_results if r.get("hard") == 0),
                "failures": rollout_failure_records(rollout_results),
            }, f, indent=2, ensure_ascii=False)

    if patches is not None:
        with open(os.path.join(step_dir, "reflect_patches.json"), "w", encoding="utf-8") as f:
            json.dump(patches, f, indent=2, ensure_ascii=False)

    if rejected_edits:
        with open(os.path.join(step_dir, "rejected_edits.json"), "w", encoding="utf-8") as f:
            json.dump(rejected_edits, f, indent=2, ensure_ascii=False)

    if ranked:
        proposals = [ranked_edit_to_proposal(r) for r in ranked]
        with open(os.path.join(step_dir, "edit_proposals.json"), "w", encoding="utf-8") as f:
            json.dump(proposals, f, indent=2, ensure_ascii=False)
        with open(os.path.join(step_dir, "edit_apply_report.json"), "w", encoding="utf-8") as f:
            json.dump({
                "gate_action": action,
                "candidate_hard": candidate_hard,
                "candidate_soft": candidate_soft,
                "per_edit": edit_reports,
            }, f, indent=2, ensure_ascii=False)
        with open(os.path.join(step_dir, "eval_results.json"), "w", encoding="utf-8") as f:
            json.dump({
                "hard": candidate_hard,
                "soft": candidate_soft,
                "action": action,
            }, f, indent=2)

        final_skill = candidate_skill if action != "reject" else prev_skill
        skills_dir = os.path.join(output_dir, "skills")
        os.makedirs(skills_dir, exist_ok=True)
        with open(os.path.join(skills_dir, f"skill_v{step:04d}.md"), "w", encoding="utf-8") as f:
            f.write(final_skill)
    logger.debug("Saved step %d artifacts", step)

def _save_slow_update_artifacts(
    output_dir: str, epoch: int, slow_result: dict,
    prev_skill: str, curr_skill: str,
) -> None:
    ep_dir = os.path.join(output_dir, "slow_update", f"epoch_{epoch:02d}")
    os.makedirs(ep_dir, exist_ok=True)
    with open(os.path.join(ep_dir, "slow_update.json"), "w") as f:
        json.dump({
            "comparison_pairs": slow_result.get("comparison_pairs", {}),
            "content": slow_result.get("slow_update_content", ""),
            "action": slow_result.get("action", ""),
        }, f, indent=2, ensure_ascii=False)
    with open(os.path.join(ep_dir, "prev_skill.md"), "w") as f:
        f.write(prev_skill)
    with open(os.path.join(ep_dir, "curr_skill.md"), "w") as f:
        f.write(curr_skill)
    logger.debug("Saved slow_update epoch %d artifacts", epoch)

def _save_meta_skill_artifacts(
    output_dir: str, epoch: int, meta_skill,
) -> None:
    ep_dir = os.path.join(output_dir, "meta_skill", f"epoch_{epoch:02d}")
    os.makedirs(ep_dir, exist_ok=True)
    with open(os.path.join(ep_dir, "meta_context.md"), "w") as f:
        f.write(meta_skill.render())
