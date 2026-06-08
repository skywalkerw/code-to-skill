"""M4 LLM 组件：Reflect（轨迹分析生成 patch）和 Select（编辑排序）。

当 LLM backend 不可用时，自动降级为规则模式。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .code_evidence import ReflectEvidenceMetrics

from code_to_skill.model_provider.llm_backend import create_llm_backend, is_llm_available
from code_to_skill.model_provider.types import InteractionRequest
from code_to_skill.model_provider.structured_output import invoke_with_structured_output

from .json_utils import safe_json_parse
from .reflect_helpers import (
    BOUNDARY_FOCUS,
    CODE_TOOLS_REFLECT_HINT,
    PRIMARY_FOCUS,
    REFLECT_SUCCESS_PROMPT,
    REFLECT_SYNTHESIS_HINT,
    REFLECT_SYSTEM_PROMPT,
    REFLECT_USER_PROMPT,
    SELECT_PROMPT,
    build_reflect_focus_hint,
    find_insert_target,
    is_numeric_check,
    reflect_stage_for_focus,
    resolve_reflect_focus,
    rule_section_heading,
    semantic_rule_for_check,
    skill_compact_for_reflect,
    skill_section_index,
    split_failure_groups,
)
from .token_budgets import get_token_budgets
from .types import EditOp

logger = logging.getLogger(__name__)


@dataclass
class ReflectLLMResult:
    patches: list[dict] = field(default_factory=list)
    evidence_metrics: ReflectEvidenceMetrics = field(default_factory=ReflectEvidenceMetrics)
    reflect_tool_rounds_max: int = 0
    custom_reflect_prompt: bool = False
    scenario_rules_triggered: int = 0

_MAX_RULE_CHECKS = 5

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


def _parse_reflect_response(response) -> dict | None:
    """从 LLM 响应提取 patch JSON（兼容 parsed 字段与纯 content）。"""
    if response.parsed and isinstance(response.parsed, dict):
        return response.parsed
    if response.content:
        parsed = safe_json_parse(response.content)
        if isinstance(parsed, dict):
            return parsed
    return None


def _sanitize_llm_edits(
    edits: list,
    current_skill: str = "",
    failed_results: list[dict] | None = None,
) -> list[dict]:
    """过滤 LLM 返回的空编辑与无效条目。"""
    from .edit_validator import validate_edit
    from .edit_traceability import infer_edit_traceability

    valid: list[dict] = []
    for e in edits:
        if not isinstance(e, dict):
            continue
        content = (e.get("content") or "").strip()
        if not content:
            continue
        edit_op = EditOp(**{k: v for k, v in e.items() if k in EditOp.model_fields})
        ok, reason = validate_edit(edit_op, current_skill)
        if ok:
            annotated = dict(e)
            if failed_results:
                infer_edit_traceability(annotated, failed_results)
            valid.append(annotated)
        else:
            logger.info("[reflect] drop invalid LLM edit: %s — %s", content[:40], reason)
    return valid


def _patches_have_edits(patches: list[dict]) -> bool:
    return any(p.get("edits") for p in patches)


def _reflect_response_usable(response) -> bool:
    if not response:
        return False
    parsed = _parse_reflect_response(response)
    return bool(parsed and parsed.get("edits"))


def _invoke_reflect_with_retry(
    backend,
    request: InteractionRequest,
    code_tools: Any = None,
    max_tool_rounds: int = 5,
    retries: int = 2,
):
    """调用 reflect；支持代码工具多轮调用，空响应时重试并加大 max_output_tokens。"""
    from code_to_skill.model_provider.tool_loop import invoke_with_tool_loop

    last_response = None
    budgets = get_token_budgets()
    token_budgets = [request.max_output_tokens, *budgets.reflect_retry]
    use_code_tools = code_tools is not None and getattr(code_tools, "enabled", False)

    for attempt in range(retries + 1):
        req = request
        if attempt > 0:
            budget = token_budgets[min(attempt, len(token_budgets) - 1)]
            req = InteractionRequest(
                **{
                    **request.model_dump(),
                    "max_output_tokens": budget,
                    "temperature": 0.1,
                    "messages": [
                        *request.messages[:-1],
                        {
                            **request.messages[-1],
                            "content": (
                                request.messages[-1]["content"]
                                + "\n\nIMPORTANT: reasoning ≤ 1 sentence. Output compact JSON only."
                            ),
                        },
                    ],
                }
            )

        if use_code_tools and attempt == 0:
            response = invoke_with_tool_loop(backend, req, code_tools, max_rounds=max_tool_rounds)
            last_response = response
            if _reflect_response_usable(response):
                return response
            synth_req = InteractionRequest(
                **{
                    **req.model_dump(),
                    "messages": [
                        *req.messages[:-1],
                        {
                            **req.messages[-1],
                            "content": req.messages[-1]["content"] + "\n\n" + REFLECT_SYNTHESIS_HINT,
                        },
                    ],
                    "tools": [],
                }
            )
            response = invoke_with_structured_output(backend, synth_req, target_schema=PATCH_SCHEMA)
            last_response = response
            if _reflect_response_usable(response):
                logger.info("[reflect] synthesis pass produced valid JSON edits")
                return response
        else:
            response = invoke_with_structured_output(backend, req, target_schema=PATCH_SCHEMA)
            last_response = response
            if _reflect_response_usable(response):
                return response

        reason = getattr(response, "finish_reason", "unknown")
        logger.warning(
            "[reflect] unusable response (finish_reason=%s), retry %d/%d",
            reason,
            attempt + 1,
            retries,
        )
    return last_response


def _reflect_failure_group(
    failed_group: list[dict],
    *,
    current_skill: str,
    buffer_summary: str,
    meta_skill_context: str,
    backend: Any,
    code_tools: Any,
    max_tool_rounds: int,
    focus: str,
    graph_sidecars: Any = None,
    adapter: Any = None,
) -> tuple[dict | None, ReflectEvidenceMetrics]:
    """对单类失败（primary / boundary）调用 reflect。"""
    empty_metrics = ReflectEvidenceMetrics()
    if not failed_group:
        return None, empty_metrics

    extra_hint = build_reflect_focus_hint(focus)
    failure_text = _format_failure_cases(failed_group[:8])
    custom_error = (
        adapter.get_error_reflect_prompt()
        if adapter is not None and getattr(adapter, "uses_custom_reflect_prompt", False)
        else ""
    )
    if custom_error:
        logger.info("[reflect] using project reflect_prompts.error override")
        system_content = custom_error.format(
            current_skill=skill_compact_for_reflect(current_skill),
            failure_text=failure_text[:3000],
            step_buffer_summary=buffer_summary,
            section_index=skill_section_index(current_skill),
        ) + extra_hint
        user_content = failure_text[:3000]
    else:
        system_content = REFLECT_SYSTEM_PROMPT.format(
            section_index=skill_section_index(current_skill),
            step_buffer_summary=buffer_summary,
        ) + extra_hint
        user_content = REFLECT_USER_PROMPT.format(
            current_skill=skill_compact_for_reflect(current_skill),
            failure_text=failure_text[:3000],
        )
    if meta_skill_context.strip():
        system_content = meta_skill_context + "\n---\n" + system_content
    if code_tools is not None and getattr(code_tools, "enabled", False):
        system_content += CODE_TOOLS_REFLECT_HINT

    from .code_evidence import build_reflect_code_evidence

    evidence_result = build_reflect_code_evidence(
        failed_group, code_tools, sidecars=graph_sidecars,
    )
    if evidence_result.text:
        user_content += "\n\n" + evidence_result.text[:4500]

    stage = reflect_stage_for_focus(focus)
    request = InteractionRequest(
        role="optimizer",
        stage=stage,
        messages=[
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ],
        max_output_tokens=get_token_budgets().reflect_failure,
        temperature=0.2,
    )

    try:
        response = _invoke_reflect_with_retry(
            backend, request, code_tools=code_tools, max_tool_rounds=max_tool_rounds,
        )
        parsed = _parse_reflect_response(response) if response else None
        if not parsed:
            return None, evidence_result.metrics
        edits = _sanitize_llm_edits(
            parsed.get("edits", []), current_skill, failed_results=failed_group,
        )
        if not edits:
            logger.warning("[reflect] %s: LLM returned no valid edits", focus)
            return None, evidence_result.metrics
        from .edit_traceability import missed_check_summary

        return {
            "source_type": "failure",
            "batch_size": len(failed_group),
            "reasoning": parsed.get("reasoning", ""),
            "edits": edits,
            "failure_summary": missed_check_summary(failed_group),
            "focus": focus,
        }, evidence_result.metrics
    except Exception as e:
        logger.warning("LLM reflect %s failed: %s", focus, e)
        return None, evidence_result.metrics


def reflect_llm(
    rollout_results: list[dict],
    current_skill: str,
    step_buffer: list[dict] | None = None,
    rejected_edits: list | None = None,
    meta_skill_context: str = "",
    backend: Any = None,
    code_tools: Any = None,
    max_tool_rounds: int = 5,
    graph_sidecars: Any = None,
    adapter: Any = None,
) -> ReflectLLMResult:
    """LLM Reflect：分析 rollout 轨迹，生成有意义的 patch。"""
    if not is_llm_available():
        logger.info("LLM not available for reflect, using rule-based")
        return ReflectLLMResult(patches=_rule_based_patches(rollout_results, current_skill))

    if backend is None:
        backend = create_llm_backend()

    buffer_summary = _build_buffer_summary(step_buffer, rejected_edits)
    failed = [r for r in rollout_results if r.get("hard", 0) == 0]
    succeeded = [r for r in rollout_results if r.get("hard", 1) == 1]
    patches: list[dict] = []
    evidence_metrics = ReflectEvidenceMetrics()
    tool_rounds_max = max_tool_rounds if (
        code_tools is not None and getattr(code_tools, "enabled", False)
    ) else 0

    if failed:
        primary_failed, boundary_failed = split_failure_groups(failed)
        logger.info(
            "[reflect] failure split: primary=%d boundary=%d",
            len(primary_failed), len(boundary_failed),
        )
        for focus, group in ((PRIMARY_FOCUS, primary_failed), (BOUNDARY_FOCUS, boundary_failed)):
            patch, group_metrics = _reflect_failure_group(
                group,
                current_skill=current_skill,
                buffer_summary=buffer_summary,
                meta_skill_context=meta_skill_context,
                backend=backend,
                code_tools=code_tools,
                max_tool_rounds=max_tool_rounds,
                focus=focus,
                graph_sidecars=graph_sidecars,
                adapter=adapter,
            )
            evidence_metrics.merge(group_metrics)
            if patch:
                patches.append(patch)

    if succeeded and not _patches_have_edits(patches):
        success_text = "\n".join([f"- {r.get('id', '?')}: PASS" for r in succeeded[:5]])
        success_system = REFLECT_SUCCESS_PROMPT
        if adapter is not None and getattr(adapter, "_reflect_prompt_success", ""):
            success_system = adapter.get_success_reflect_prompt()
        request = InteractionRequest(
            role="optimizer",
            stage="reflect_success",
            messages=[
                {"role": "system", "content": success_system},
                {"role": "user", "content": success_text[:1500]},
            ],
            max_output_tokens=get_token_budgets().reflect_success,
            temperature=0.2,
        )
        try:
            response = invoke_with_structured_output(backend, request, target_schema=PATCH_SCHEMA)
            parsed = _parse_reflect_response(response)
            if parsed:
                edits = _sanitize_llm_edits(parsed.get("edits", []), current_skill)
                if edits:
                    patches.append({
                        "source_type": "success",
                        "batch_size": len(succeeded),
                        "reasoning": parsed.get("reasoning", ""),
                        "edits": edits,
                    })
        except Exception as e:
            logger.warning("LLM reflect success analysis failed: %s", e)

    custom_prompt = (
        adapter is not None and getattr(adapter, "uses_custom_reflect_prompt", False)
    )
    if _patches_have_edits(patches):
        return ReflectLLMResult(
            patches=patches,
            evidence_metrics=evidence_metrics,
            reflect_tool_rounds_max=tool_rounds_max,
            custom_reflect_prompt=custom_prompt,
        )

    logger.info("[reflect] falling back to rule-based patches")
    return ReflectLLMResult(
        patches=_rule_based_patches(rollout_results, current_skill),
        evidence_metrics=evidence_metrics,
        reflect_tool_rounds_max=tool_rounds_max,
        custom_reflect_prompt=custom_prompt,
    )


def select_edits_llm(
    edits: list[EditOp],
    current_skill: str,
    budget: int = 3,
    backend: Any = None,
    rollout_results: list[dict] | None = None,
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
        return _rank_edits_by_coverage(edits, rollout_results or [], budget)

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
            "content": SELECT_PROMPT.format(
                current_skill=current_skill[:1500],
                edits=edit_text[:2000],
                budget=budget,
            )
        }],
        max_output_tokens=get_token_budgets().select_edits,
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

    return _rank_edits_by_coverage(edits, rollout_results or [], budget)


def _format_failure_cases(failed: list[dict]) -> str:
    """格式化失败 case 供 Reflect 使用。"""
    parts: list[str] = []
    for r in failed:
        parts.append(f"\n### Task {r.get('id', '?')}")
        parts.append(f"Question: {r.get('question', '')[:300]}")
        parts.append(f"Task type: {r.get('task_type', '')}")
        passed = r.get("passed_checks", [])
        missed = r.get("missed_checks", [])
        if passed:
            parts.append(f"Passed checks: {', '.join(passed)}")
        if missed:
            parts.append(f"Missed checks: {', '.join(missed)}")
        parts.append(f"Fail reason: {r.get('fail_reason', 'unknown')}")
        parts.append(f"Answer excerpt: {r.get('predicted_answer', '')[:400]}")
    return "\n".join(parts)


def _group_failures_by_task_type(failed: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for r in failed:
        tt = r.get("task_type") or "default"
        groups.setdefault(tt, []).append(r)
    return groups


def _rule_bullet_in_skill(rule_line: str, skill: str) -> bool:
    line = rule_line.strip()
    if line in skill:
        return True
    return line.lstrip("- ").strip() in skill


def _last_line_in_section(skill: str, heading: str) -> str:
    """返回 section 内最后一行可插入锚点（末条 bullet 或 heading 本身）。"""
    idx = skill.find(heading)
    if idx < 0:
        return heading
    rest = skill[idx + len(heading):]
    last: str | None = None
    for ln in rest.splitlines():
        stripped = ln.strip()
        if stripped.startswith("#"):
            break
        if stripped.startswith("-") or stripped.startswith("|"):
            last = stripped
    return last or heading


def _rule_based_patches(results: list[dict], current_skill: str = "") -> list[dict]:
    """规则降级：按 task_type 分组，生成语义化规则（非关键词堆砌）。"""
    failed = [r for r in results if r.get("hard", 0) == 0]
    if not failed:
        return []

    edits: list[dict] = []
    groups = _group_failures_by_task_type(failed)

    for task_type, group in groups.items():
        missed_counts: dict[str, int] = {}
        group_focus = resolve_reflect_focus(group[0]) if group else PRIMARY_FOCUS
        for r in group:
            for check in r.get("missed_checks", []):
                if is_numeric_check(check):
                    missed_counts["_amount_"] = missed_counts.get("_amount_", 0) + 1
                else:
                    missed_counts[check] = missed_counts.get(check, 0) + 1

        if not missed_counts:
            continue

        top_checks = sorted(
            [(c, n) for c, n in missed_counts.items() if c != "_amount_"],
            key=lambda x: -x[1],
        )[:_MAX_RULE_CHECKS]

        rules: list[str] = []
        seen_rules: set[str] = set()
        for check, _count in top_checks:
            rule = semantic_rule_for_check(check)
            if rule not in seen_rules:
                rules.append(f"- {rule}")
                seen_rules.add(rule)
        if "_amount_" in missed_counts:
            numeric_checks: list[str] = []
            for r in group:
                for check in r.get("missed_checks", []):
                    if is_numeric_check(check) and check not in numeric_checks:
                        numeric_checks.append(check)
            for check in numeric_checks[:2]:
                rule = semantic_rule_for_check(check)
                if rule not in seen_rules:
                    rules.append(f"- {rule}")
                    seen_rules.add(rule)

        new_rules = [r for r in rules if not _rule_bullet_in_skill(r, current_skill)]

        if not new_rules and group:
            r0 = group[0]
            missed = r0.get("missed_checks", [])[:4]
            hint = (
                f"- For task_type={task_type}: cover verification checks "
                f"{', '.join(missed)}"
            )
            if not _rule_bullet_in_skill(hint, current_skill):
                new_rules = [hint]

        if not new_rules:
            continue

        target = find_insert_target(current_skill, group_focus)
        heading = rule_section_heading(group_focus)

        if heading in current_skill:
            anchor = _last_line_in_section(current_skill, heading)
            content = "\n".join(new_rules)
            edit: dict = {
                "op": "insert_after",
                "target": anchor,
                "content": content,
                "source_type": "failure",
            }
        else:
            content = heading + "\n\n" + "\n".join(new_rules)
            edit = {
                "op": "insert_after" if target else "append",
                "content": content,
                "source_type": "failure",
            }
            if target:
                edit["target"] = target

        from .edit_traceability import annotate_rule_edit

        missed_for_edit = [c for c, _ in top_checks]
        if "_amount_" in missed_counts:
            missed_for_edit.append("_amount_")
        annotate_rule_edit(
            edit,
            task_ids=[r.get("id", "") for r in group],
            missed_checks=missed_for_edit,
        )
        edits.append(edit)

    if not edits:
        return []

    from .edit_traceability import missed_check_summary

    return [{
        "source_type": "failure",
        "batch_size": len(failed),
        "failure_summary": missed_check_summary(failed),
        "edits": edits[:2],
    }]


def _collect_missed_checks(results: list[dict]) -> set[str]:
    missed: set[str] = set()
    for r in results:
        if r.get("hard", 0) == 0:
            missed.update(r.get("missed_checks", []))
    return missed


def _score_edit_coverage(edit: EditOp, missed: set[str]) -> float:
    """估算编辑对 missed checks 的覆盖度。"""
    content = (edit.content or "").lower()
    if not missed:
        return 0.5
    hits = sum(1 for c in missed if c.lower() in content)
    coverage = hits / len(missed)
    loc_bonus = 0.15 if edit.op in ("insert_after", "replace") and edit.target else 0.0
    return coverage + loc_bonus


def _rank_edits_by_coverage(
    edits: list[EditOp],
    rollout_results: list[dict],
    budget: int,
) -> list[dict]:
    """按 missed checks 覆盖度排序编辑。"""
    missed = _collect_missed_checks(rollout_results)
    scored = sorted(
        [(e, _score_edit_coverage(e, missed)) for e in edits],
        key=lambda x: -x[1],
    )
    return [
        {"edit": e, "rank": i + 1, "support_count": 1, "score": round(score, 3)}
        for i, (e, score) in enumerate(scored[:budget])
    ]


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
