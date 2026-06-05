"""SkillOpt 环境适配器抽象基类。

对齐 external/SkillOpt skillopt/envs/base.py。

设计原则：
- Adapter 封装所有 benchmark-specific 逻辑（rollout 执行、reflect 分析），trainer 不感知细节。
- 支持 context_mode (inline / agent_read / none) 控制上下文注入方式。
- 两层 prompt 系统：env 级可覆盖通用 Reflect 模板。
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..step_buffer import StepBufferManager

logger = logging.getLogger(__name__)

# ── 默认 Reflect prompt 路径（按 env 名自动解析）─────────
_DEFAULT_ERROR_PROMPT = """## Task
Analyze the failure cases and propose specific edits to improve the Skill document.

## Current Skill
{current_skill}

## Failure Cases
{failure_text}

## Step Buffer (previously rejected edits — do NOT repeat)
{step_buffer_summary}

## Instructions
1. Identify the ROOT CAUSE pattern in the failures (not individual edge cases).
2. Propose 1-3 specific edits to the Skill (append new rules, clarify constraints, add verification steps).
3. Each edit must have: op (append/replace/insert_after/delete), content (the new text to add).
4. Do NOT propose edits that have been previously rejected (see Step Buffer above).

CRITICAL: Do NOT remove existing rules unless they are contradictory. Prefer appending new rules.

## Output
Return JSON: {{"reasoning": "...", "edits": [{{"op": "append", "content": "...", "source_type": "failure"}}]}}"""

_DEFAULT_SUCCESS_PROMPT = """## Task
Based on the successful cases, propose edits to retain effective rules.

## Successful Cases
{success_text}

## Instructions
If the Skill successfully handled these cases, consider adding a note to preserve the effective patterns.
Only propose edits for patterns NOT already covered in the skill.

## Output
Return JSON: {{"reasoning": "...", "edits": []}}"""


class EnvAdapter(ABC):
    """SkillOpt 环境适配器抽象基类。

    每个 benchmark 需要实现自己的 adapter 子类，至少实现 rollout 和 get_reflect_prompts。
    reflect 默认使用通用 minibatch 分析流程，子类可以覆盖。
    """

    def __init__(self, env_name: str = "DEFAULT"):
        self.env_name = env_name

    # ── 生命周期 ──────────────────────────────────────────

    def setup(self, cfg: dict | None = None) -> None:
        """适配器初始化（可选覆盖）。在训练开始前调用一次。"""
        pass

    # ── 核心接口（子类必须实现）────────────────────────────

    @abstractmethod
    def rollout(
        self,
        skill: str,
        items: list[dict],
        target_backend: Any = None,
        out_dir: str = "",
    ) -> list[dict]:
        """用当前 Skill 对一批 benchmark items 执行 rollout。

        Args:
            skill: 当前 Skill 文档内容
            items: benchmark item 列表，每条至少含 id / question / expected_checks
            target_backend: 目标模型后端（如 InteractionBackend 实例）
            out_dir: 产物输出目录

        Returns:
            rollout 结果列表，每条含 id / hard / soft / predicted_answer / fail_reason 等
        """
        ...

    @abstractmethod
    def evaluate(
        self,
        skill: str,
        items: list[dict],
        target_backend: Any = None,
    ) -> dict:
        """在 selection/test split 上评估 Skill 分数。

        Returns:
            {"soft": float, "accuracy": float, "f1": float}
        """
        ...

    # ── Reflect 相关（子类可选覆盖 prompt）─────────────────

    def get_error_reflect_prompt(self) -> str:
        """返回失败分析 prompt 模板（两层：env 覆盖 > 默认）。"""
        return _DEFAULT_ERROR_PROMPT

    def get_success_reflect_prompt(self) -> str:
        """返回成功分析 prompt 模板。"""
        return _DEFAULT_SUCCESS_PROMPT

    def get_task_types(self) -> list[str]:
        """返回本 benchmark 的任务类型列表。"""
        return ["default"]

    # ── 辅助 ──────────────────────────────────────────────

    @staticmethod
    def _build_context_from_item(item: dict, context_mode: str = "inline") -> str:
        """根据 context_mode 从 item 中提取上下文。

        当前仓库的 benchmark 格式是 item 自带 expected_checks 作为评分标准。
        context_refs 是预留字段，供后续 adapter 实现 inline / agent_read 模式。
        """
        question = item.get("task_template", item.get("question", ""))
        refs = item.get("context_refs", [])
        mode = item.get("context_mode", context_mode)

        if mode == "inline" and refs:
            refs_str = "\n".join([f"- {r}" for r in refs])
            return f"Context references:\n{refs_str}\n\nTask:\n{question}"
        return question


class DEFAULTAdapter(EnvAdapter):
    """内置默认适配器。

    适配当前仓库的 benchmark 格式：
    - items: [{id, question/task_template, expected_checks, task_type}]
    - rollout: 用 M5 model_provider 或关键词规则模拟
    - evaluate: 用确定性 keyword scorer

    这是可工作的默认实现；后续可按需要覆盖子类化。
    """

    def __init__(self, use_llm: bool = False):
        super().__init__(env_name="DEFAULT")
        self.use_llm = use_llm
        self._backend = None

    def setup(self, cfg: dict | None = None) -> None:
        if self.use_llm:
            try:
                from code_to_skill.model_provider.llm_backend import (
                    is_llm_available,
                    create_llm_backend,
                )
                if is_llm_available():
                    self._backend = create_llm_backend()
            except Exception:
                logger.info("LLM backend not available for DEFAULTAdapter; using rule-based rollout")

    def rollout(
        self,
        skill: str,
        items: list[dict],
        target_backend: Any = None,
        out_dir: str = "",
    ) -> list[dict]:
        """默认 rollout：LLM 优先，降级关键词规则。"""
        from ..scoring import score_rollout_result  # local import to avoid circular

        backend = target_backend or self._backend
        results: list[dict] = []

        for item in items:
            checks = item.get("expected_checks", [])
            question = self._build_context_from_item(item)

            if backend:
                from code_to_skill.model_provider.types import InteractionRequest
                try:
                    resp = backend.invoke(InteractionRequest(
                        role="target",
                        stage="rollout",
                        messages=[
                            {
                                "role": "system",
                                "content": (
                                    "You are an expert code reviewer. "
                                    f"Use this skill:\n\n{skill[:2000]}"
                                ),
                            },
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
                # 规则模拟
                relevant_lines = [
                    line for line in skill.split("\n")
                    if any(c.lower() in line.lower() for c in checks)
                ]
                relevant_text = "\n".join(relevant_lines[:10]) if relevant_lines else skill[:300]
                predicted = f"基于以下规则分析：\n{relevant_text}\n\n检查项: {', '.join(checks)}"
                fail_reason = ""

            scores = score_rollout_result(predicted, checks)
            results.append({
                "id": item.get("id", ""),
                "hard": scores["hard"],
                "soft": scores["soft"],
                "accuracy": scores.get("accuracy", 0.0),
                "precision": scores.get("precision", 0.0),
                "recall": scores.get("recall", 0.0),
                "f1": scores.get("f1", 0.0),
                "predicted_answer": predicted,
                "fail_reason": fail_reason or ("check_missed" if scores["hard"] == 0 else ""),
                "task_type": item.get("task_type", ""),
            })

        return results

    def evaluate(
        self,
        skill: str,
        items: list[dict],
        target_backend: Any = None,
    ) -> dict:
        """默认评估：在 selection/test split 上算分。"""
        if not items:
            return {"soft": 0.0, "accuracy": 0.0, "f1": 0.0}
        results = self.rollout(skill, items, target_backend=target_backend)
        n = max(len(results), 1)
        return {
            "soft": round(sum(r["soft"] for r in results) / n, 3),
            "accuracy": round(sum(r["accuracy"] for r in results) / n, 3),
            "f1": round(sum(r["f1"] for r in results) / n, 3),
        }

    def get_task_types(self) -> list[str]:
        return ["code_review", "qa", "default"]
