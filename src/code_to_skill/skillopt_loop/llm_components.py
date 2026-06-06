"""M4 LLM 组件：Reflect（轨迹分析生成 patch）和 Select（编辑排序）。

当 LLM backend 不可用时，自动降级为规则模式。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from code_to_skill.model_provider.llm_backend import create_llm_backend, is_llm_available
from code_to_skill.model_provider.types import InteractionRequest
from code_to_skill.model_provider.structured_output import invoke_with_structured_output

from .json_utils import safe_json_parse
from .token_budgets import get_token_budgets
from .types import EditOp

logger = logging.getLogger(__name__)

# missed check → 语义化规则（避免纯关键词堆砌）
_CHECK_SEMANTIC_RULES: dict[str, str] = {
    "会计凭证": "输出必须以「## 会计凭证」为标题",
    "借": "分录表格须包含借方行，「借贷」列标注「借」",
    "贷": "分录表格须包含贷方行，「借贷」列标注「贷」",
    "借贷校验": "凭证末尾须输出「借贷校验」行（借方合计 = 贷方合计 ✓）",
    "库存": "购入/存货交易：借方科目名称须含「库存」",
    "银行": "付款类交易：贷方科目名称须含「银行」",
    "现金": "收款/付款交易：涉及现金的科目名称须含「现金」",
    "贷款": "贷款发放：借方科目须含「贷款」",
    "发放": "贷款发放交易：交易摘要须含「发放」",
    "还款": "还款交易：交易摘要须含「还款」",
    "利息": "利息相关：须区分利息科目与本金科目",
    "收入": "利息/费用收入：贷方须含「收入」类科目",
    "费用": "费用扣款：须含「费用」科目",
    "Charge": "费用类交易：须引用 Charge 费用类型",
    "计提": "计提交易：交易类型标注「计提」",
    "应收利息": "利息计提：借方须含「应收利息」",
    "销售": "销售交易：交易摘要须含「销售」",
    "资产": "资产购入：借方须含「资产」类科目",
    "储蓄": "储蓄账户：须含「储蓄」科目",
    "待确认": "信息不足时：不确定字段填「[待确认]」，但仍须输出完整凭证表格",
    "补充": "信息不足时：列出缺失项后须追问用户补充",
    "会计口径": "还款未拆分本金/利息时：须追问会计口径（CashBased/AccrualBased）",
    "产品": "缺少产品 ID 时：须追问关联产品或 GL Account 配置",
    "凭证": "任何场景均须输出会计凭证，不得仅返回缺失清单",
    "借贷平衡": "不得输出借贷不平的凭证",
    "不平": "借贷金额不等时：拒绝输出并说明须满足 isBalanced()",
    "isBalanced": "须确保 isBalanced() 为 true 方可输出",
}

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


REFLECT_SYNTHESIS_HINT = (
    "Stop using tools. Output JSON only with schema: "
    '{"reasoning": "one sentence", "edits": [{"op": "append|replace|prepend", '
    '"target": "section or empty", "content": "concrete skill rule"}]}. '
    "At least one edit must address the rollout failure checks."
)


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


_CODE_TOOLS_REFLECT_HINT = """
## Code Reading Tools
File tools: search_code, read_code_file, list_code_files.
Graph tools (if available): explore_symbol, get_symbol_source, get_symbol_node, find_callers,
find_callees, list_graph_files, search_symbol, get_code_context, trace_symbol, impact_symbol,
graph_status.
Prefer explore_symbol on benchmark context_refs (e.g. AccountingProcessor#createJournalEntries).
Use trace_symbol with to_symbol to verify call chains (returns paths_to.summary like A → B → C).
Use from_entry=rest when tracing from API entry to a business handler.
Ground skill edits in real code: cite file paths + method names from graph results.
After research, respond with JSON only (no more tool calls).
"""


def reflect_llm(
    rollout_results: list[dict],
    current_skill: str,
    step_buffer: list[dict] | None = None,
    rejected_edits: list | None = None,
    meta_skill_context: str = "",
    backend: Any = None,
    code_tools: Any = None,
    max_tool_rounds: int = 5,
) -> list[dict]:
    """LLM Reflect：分析 rollout 轨迹，生成有意义的 patch。"""
    if not is_llm_available():
        logger.info("LLM not available for reflect, using rule-based")
        return _rule_based_patches(rollout_results, current_skill)

    if backend is None:
        backend = create_llm_backend()

    buffer_summary = _build_buffer_summary(step_buffer, rejected_edits)
    failed = [r for r in rollout_results if r.get("hard", 0) == 0]
    succeeded = [r for r in rollout_results if r.get("hard", 1) == 1]
    patches: list[dict] = []

    if failed:
        failure_text = _format_failure_cases(failed[:8])
        system_content = _REFLECT_SYSTEM_PROMPT.format(
            section_index=_skill_section_index(current_skill),
            step_buffer_summary=buffer_summary,
        )
        if meta_skill_context.strip():
            system_content = meta_skill_context + "\n---\n" + system_content
        if code_tools is not None and getattr(code_tools, "enabled", False):
            system_content += _CODE_TOOLS_REFLECT_HINT

        from .code_evidence import build_reflect_code_evidence

        code_evidence = build_reflect_code_evidence(failed, code_tools)
        user_content = _REFLECT_USER_PROMPT.format(
            current_skill=_skill_compact_for_reflect(current_skill),
            failure_text=failure_text[:3000],
        )
        if code_evidence:
            user_content += "\n\n" + code_evidence[:4500]

        request = InteractionRequest(
            role="optimizer",
            stage="reflect_failure",
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
            if parsed:
                edits = _sanitize_llm_edits(
                    parsed.get("edits", []), current_skill, failed_results=failed,
                )
                if edits:
                    from .edit_traceability import missed_check_summary

                    patches.append({
                        "source_type": "failure",
                        "batch_size": len(failed),
                        "reasoning": parsed.get("reasoning", ""),
                        "edits": edits,
                        "failure_summary": missed_check_summary(failed),
                    })
                else:
                    logger.warning("[reflect] LLM returned no valid edits")
        except Exception as e:
            logger.warning("LLM reflect failure analysis failed: %s", e)

    if succeeded and not _patches_have_edits(patches):
        success_text = "\n".join([f"- {r.get('id', '?')}: PASS" for r in succeeded[:5]])
        request = InteractionRequest(
            role="optimizer",
            stage="reflect_success",
            messages=[
                {"role": "system", "content": _REFLECT_SUCCESS_PROMPT},
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

    if _patches_have_edits(patches):
        return patches

    logger.info("[reflect] falling back to rule-based patches")
    return _rule_based_patches(rollout_results, current_skill)


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
            "content": _SELECT_PROMPT.format(
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


def _skill_section_index(skill: str) -> str:
    """提取 skill 章节标题，供 insert_after/replace 定位。"""
    headers = re.findall(r"^#{1,3}\s+.+", skill, re.M)
    if not headers:
        return "(no section headers found)"
    return "\n".join(f"- {h.strip()}" for h in headers[:20])


def _skill_compact_for_reflect(skill: str, max_chars: int = 1500) -> str:
    """Reflect 用精简 skill 摘要，避免 prompt 过长导致输出 token 耗尽。"""
    markers = (
        "### 2.3 生成会计凭证",
        "## 三、必须遵守的约束",
        "## 四、禁止行为",
        "## 五、信息不足时的处理",
        "## 六、验证检查清单",
    )
    chunks: list[str] = []
    for marker in markers:
        idx = skill.find(marker)
        if idx < 0:
            continue
        end = len(skill)
        for other in markers:
            if other == marker:
                continue
            nxt = skill.find(other, idx + len(marker))
            if 0 <= nxt < end:
                end = nxt
        chunk = skill[idx:end].strip()
        if chunk:
            chunks.append(chunk[:500])

    if chunks:
        compact = "\n\n---\n\n".join(chunks)
    else:
        compact = skill[:max_chars]

    return compact[:max_chars]


def _find_insert_target(current_skill: str, missed_checks: list[str]) -> str:
    """根据 missed checks 选择最佳插入位置。"""
    if "### 2.3 生成会计凭证" in current_skill:
        return "### 2.3 生成会计凭证"
    if "## 三、必须遵守的约束" in current_skill:
        return "## 三、必须遵守的约束"
    if "## 六、验证检查清单" in current_skill:
        return "## 六、验证检查清单"
    headers = re.findall(r"^#{1,3}\s+.+", current_skill, re.M)
    return headers[-1].strip() if headers else ""


def _is_amount_check(check: str) -> bool:
    """纯数字金额不作为关键词规则。"""
    return bool(re.fullmatch(r"[\d.]+", check.strip()))


def _semantic_rule_for_check(check: str) -> str:
    """将 missed check 转为语义化规则。"""
    if check in _CHECK_SEMANTIC_RULES:
        return _CHECK_SEMANTIC_RULES[check]
    if _is_amount_check(check):
        return "金额列须填写交易中的具体金额数值"
    return f"输出须包含「{check}」"


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
        for r in group:
            for check in r.get("missed_checks", []):
                if _is_amount_check(check):
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
            rule = _semantic_rule_for_check(check)
            if rule not in seen_rules:
                rules.append(f"- {rule}")
                seen_rules.add(rule)
        if "_amount_" in missed_counts:
            rules.append("- 金额列须填写交易中的具体金额数值")

        new_rules = [r for r in rules if not _rule_bullet_in_skill(r, current_skill)]

        if not new_rules and group:
            r0 = group[0]
            missed = r0.get("missed_checks", [])[:4]
            hint = f"- 针对{task_type}场景：须覆盖 {', '.join(missed)} 等检查点"
            if not _rule_bullet_in_skill(hint, current_skill):
                new_rules = [hint]

        if not new_rules:
            continue

        if task_type == "journal_entry":
            target = "### 2.3 生成会计凭证"
            if target not in current_skill:
                target = _find_insert_target(current_skill, [c for c, _ in top_checks])
            heading = "### 分录输出要求（自动生成）"
        else:
            target = "## 五、信息不足时的处理"
            if target not in current_skill:
                target = _find_insert_target(current_skill, [c for c, _ in top_checks])
            heading = "### 边界场景规则（自动生成）"

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


# ── Prompt 模板 ─────────────────────────────────────────────

_REFLECT_SYSTEM_PROMPT = """You are a Skill document optimizer for an accounting voucher generation agent.

## Available Sections (use as insert_after target)
{section_index}

## Step Buffer (DO NOT repeat rejected edits)
{step_buffer_summary}

## Instructions
1. Analyze Missed checks — identify ROOT CAUSE per task type (journal_entry vs incomplete info).
2. Propose 1-3 edits with op="insert_after", targeting the most relevant section.
3. Each edit content: bullet rules using 必须/不得, >= 50 chars, covering missed checks semantically.
4. For journal_entry failures: add rules about voucher table format, debit/credit rows, account names.
5. For incomplete-info failures: require outputting full voucher with [待确认] placeholders.

FORBIDDEN: "# Verify", "need improvement", "TODO", keyword-only lists without actionable rules.

## Output
Return JSON only. Keep "reasoning" to ONE sentence (≤ 80 chars).
{{"reasoning": "...", "edits": [{{"op": "insert_after", "target": "### section", "content": "- 必须...", "source_type": "failure"}}]}}"""

_REFLECT_USER_PROMPT = """## Current Skill
{current_skill}

## Failure Cases
{failure_text}"""

_REFLECT_SUCCESS_PROMPT = """Based on successful cases, propose edits to preserve effective patterns.
Return JSON: {{"reasoning": "...", "edits": []}} If no changes needed, return empty edits array."""

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
