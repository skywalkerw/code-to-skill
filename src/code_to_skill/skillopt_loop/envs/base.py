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
Analyze rollout failures and propose specific edits to improve the Skill document.

## Current Skill
{current_skill}

## Failure Cases
{failure_text}

## Step Buffer (previously rejected edits — do NOT repeat)
{step_buffer_summary}

## Instructions
1. Identify the ROOT CAUSE pattern in the failures (not individual edge cases).
2. Propose 1-3 specific edits (append/insert_after/replace) with actionable rules.
3. Cover missed verification checks semantically — not keyword dumps.
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
        """返回失败分析 prompt 模板（project.reflect_prompts > 默认）。"""
        custom = getattr(self, "_reflect_prompt_error", "")
        return custom or _DEFAULT_ERROR_PROMPT

    def get_success_reflect_prompt(self) -> str:
        """返回成功分析 prompt 模板。"""
        custom = getattr(self, "_reflect_prompt_success", "")
        return custom or _DEFAULT_SUCCESS_PROMPT

    @property
    def uses_custom_reflect_prompt(self) -> bool:
        return bool(getattr(self, "_reflect_prompt_error", ""))

    def get_task_types(self) -> list[str]:
        """返回本 benchmark 的任务类型列表。"""
        return ["default"]

    # ── 辅助 ──────────────────────────────────────────────

    @staticmethod
    def _item_context_mode(item: dict, default: str = "inline") -> str:
        mode = str(item.get("context_mode") or default).strip().lower()
        if mode in ("inline", "agent_read", "none"):
            return mode
        return default

    @staticmethod
    def _build_context_from_item(item: dict, context_mode: str = "inline") -> str:
        """根据 context_mode 从 item 中提取任务文本（不含 inline 代码片段）。"""
        question = item.get("question", "")
        refs = item.get("context_refs", [])
        mode = EnvAdapter._item_context_mode(item, context_mode)

        if mode == "inline" and refs:
            refs_str = "\n".join([f"- {r}" for r in refs])
            return f"Context references:\n{refs_str}\n\nTask:\n{question}"
        if mode == "agent_read" and refs:
            refs_str = "\n".join([f"- {r}" for r in refs])
            return (
                "Use code tools to read the following references before answering:\n"
                f"{refs_str}\n\nTask:\n{question}"
            )
        return question


class DEFAULTAdapter(EnvAdapter):
    """内置默认适配器。

    适配当前仓库的 benchmark 格式：
    - items: [{id, question, expected_checks, task_type}]
    - rollout: 用 M5 model_provider 或关键词规则模拟
    - evaluate: 用确定性 keyword scorer

    这是可工作的默认实现；后续可按需要覆盖子类化。
    """

    def __init__(
        self,
        use_llm: bool = False,
        code_repos: list[dict] | None = None,
        enable_code_tools: bool = True,
        max_tool_rounds: int = 5,
        rollout_max_tool_rounds: int = 2,
        rollout_workers: int = 1,
    ):
        super().__init__(env_name="DEFAULT")
        self.use_llm = use_llm
        self._backend = None
        self._reflect_prompt_error = ""
        self._reflect_prompt_success = ""
        self._judge_backend = None
        self._global_check_aliases: dict[str, list[str]] = {}
        self.enable_code_tools = enable_code_tools
        self.max_tool_rounds = max_tool_rounds
        self.rollout_max_tool_rounds = rollout_max_tool_rounds
        self.rollout_workers = max(1, int(rollout_workers or 1))
        from code_to_skill.codegraph_mcp.handler import build_code_tools_handler
        self.code_tools = build_code_tools_handler(
            code_repos, enable_code_tools=enable_code_tools,
        )

    def setup(self, cfg: dict | None = None) -> None:
        self._reflect_prompt_error = ""
        self._reflect_prompt_success = ""
        self._judge_backend = None
        if cfg:
            from code_to_skill.codegraph_mcp.handler import build_code_tools_handler
            self.code_tools = build_code_tools_handler(
                cfg.get("code_repos"),
                enable_code_tools=self.enable_code_tools,
                graph_db_path=cfg.get("graph_db_path", ""),
                repo_root=cfg.get("repo_root", ""),
                graph_sources=cfg.get("graph_sources"),
            )
            self.max_tool_rounds = int(cfg.get("max_tool_rounds", self.max_tool_rounds))
            self.rollout_max_tool_rounds = int(
                cfg.get("rollout_max_tool_rounds", self.rollout_max_tool_rounds)
            )
            if "rollout_workers" in cfg:
                self.rollout_workers = max(1, int(cfg.get("rollout_workers") or 1))
            self.enable_code_tools = bool(cfg.get("enable_code_tools", self.enable_code_tools))
            self.graph_sidecars = cfg.get("graph_sidecars")
            prompts = cfg.get("reflect_prompts") or {}
            self._reflect_prompt_error = str(prompts.get("error") or "").strip()
            self._reflect_prompt_success = str(prompts.get("success") or "").strip()
            self._judge_backend = cfg.get("judge_backend")
            self._global_check_aliases = dict(cfg.get("check_aliases") or {})
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
        if not items:
            return []

        backend = target_backend or self._backend
        workers = min(self.rollout_workers, len(items))
        if workers <= 1:
            return [
                self._rollout_single_item(skill, item, backend)
                for item in items
            ]

        from concurrent.futures import ThreadPoolExecutor

        logger.debug("[rollout] parallel workers=%d items=%d", workers, len(items))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(
                lambda item: self._rollout_single_item(skill, item, backend),
                items,
            ))

    def _rollout_single_item(
        self,
        skill: str,
        item: dict,
        backend: Any,
    ) -> dict:
        """对单条 benchmark item 执行 rollout。"""
        from ..scoring import score_benchmark_item  # local import to avoid circular
        from ..token_budgets import get_token_budgets

        checks = item.get("expected_checks", [])
        context_mode = self._item_context_mode(item)
        question = self._build_context_from_item(item)

        if backend:
            from code_to_skill.model_provider.types import InteractionRequest
            from code_to_skill.model_provider.tool_loop import invoke_with_tool_loop
            from ..rollout_helpers import (
                assemble_rollout_user_content,
                build_rollout_synthesis_hint,
                build_tool_leak_retry_hint,
                build_rollout_system_prompt,
                build_rollout_user_message,
                extract_rollout_answer,
                fallback_predicted_from_tools,
                fallback_skill_answer,
                looks_like_tool_call_leak,
            )
            try:
                from ..code_evidence import build_rollout_item_context

                task_msg = build_rollout_user_message(question, checks, item=item)
                sidecars = getattr(self, "graph_sidecars", None)
                code_ctx = ""
                if context_mode == "inline":
                    code_ctx = build_rollout_item_context(
                        item, self.code_tools, sidecars=sidecars,
                    )
                user_msg = assemble_rollout_user_content(task_msg, code_ctx)
                code_tools_enabled = (
                    context_mode != "none"
                    and self.enable_code_tools
                    and self.code_tools.enabled
                )
                request = InteractionRequest(
                    role="target",
                    stage="rollout",
                    messages=[
                        {
                            "role": "system",
                            "content": build_rollout_system_prompt(
                                skill, code_tools_enabled=code_tools_enabled,
                            ),
                        },
                        {"role": "user", "content": user_msg},
                    ],
                    max_output_tokens=get_token_budgets().rollout,
                    temperature=0.3,
                    metadata={
                        "synthesis_hint": build_rollout_synthesis_hint(checks),
                        "leak_retry_hint": build_tool_leak_retry_hint(checks),
                    },
                )
                tool_rounds = (
                    self.rollout_max_tool_rounds if code_tools_enabled else 0
                )
                logger.debug(
                    "[rollout] item=%s context_mode=%s tool_rounds=%d (max_reflect=%d)",
                    item.get("id", "?"),
                    context_mode,
                    tool_rounds,
                    self.max_tool_rounds,
                )
                if tool_rounds > 0:
                    resp = invoke_with_tool_loop(
                        backend, request, self.code_tools, max_rounds=tool_rounds,
                    )
                else:
                    resp = backend.invoke(request)
                predicted = extract_rollout_answer((resp.content or "").strip())
                if looks_like_tool_call_leak(predicted):
                    predicted = ""
                if not predicted:
                    tool_snippets = getattr(resp, "tool_snippets", "") or ""
                    if tool_snippets:
                        predicted = fallback_predicted_from_tools(
                            tool_snippets, question, checks, skill,
                        )
                    else:
                        predicted = fallback_skill_answer(question, checks, skill)
                fail_reason = ""
            except Exception as e:
                predicted = f"[LLM error: {e}]"
                fail_reason = str(e)[:100]
        else:
            from ..rollout_helpers import fallback_skill_answer

            predicted = fallback_skill_answer(question, checks, skill)
            fail_reason = ""

        scores = score_benchmark_item(
            predicted,
            item,
            judge_backend=self._judge_backend,
            global_check_aliases=self._global_check_aliases,
        )
        missed = scores.get("missed_checks", [])
        if not fail_reason and scores["hard"] == 0 and missed:
            fail_reason = "missed: " + ", ".join(missed)
        elif not fail_reason and scores["hard"] == 0:
            fail_reason = "check_missed"

        return {
            "id": item.get("id", ""),
            "question": item.get("question", ""),
            "response_mode": item.get("response_mode", "answer"),
            "reflect_focus": item.get("reflect_focus", ""),
            "context_refs": list(item.get("context_refs") or []),
            "expected_checks": checks,
            "passed_checks": scores.get("passed_checks", []),
            "missed_checks": missed,
            "hard": scores["hard"],
            "soft": scores["soft"],
            "accuracy": scores.get("accuracy", 0.0),
            "precision": scores.get("precision", 0.0),
            "recall": scores.get("recall", 0.0),
            "f1": scores.get("f1", 0.0),
            "predicted_answer": predicted,
            "fail_reason": fail_reason,
            "task_type": item.get("task_type", ""),
        }

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
