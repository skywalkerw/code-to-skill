"""Reflect 辅助：prompt 模板与 skill/失败分组工具（通用框架，不含目标项目领域知识）。"""
from __future__ import annotations

import re

PRIMARY_FOCUS = "primary"
BOUNDARY_FOCUS = "boundary"

_BOUNDARY_RESPONSE_MODES = frozenset({"clarify", "reject"})

_SECTION_PRIORITY_KEYWORDS = (
    "workflow", "process", "output", "constraint", "validation", "rule",
    "format", "procedure", "边界", "约束", "验证", "流程", "输出",
)
_BOUNDARY_TARGET_KEYWORDS = (
    "incomplete", "clarif", "boundary", "constraint", "reject", "invalid",
    "信息", "不足", "边界", "约束", "禁止",
)
_PRIMARY_TARGET_KEYWORDS = (
    "output", "workflow", "procedure", "deliverable", "format", "task",
    "输出", "流程", "工作流", "任务",
)

RULE_SECTION_HEADING_PRIMARY = "### Output rules (auto-generated)"
RULE_SECTION_HEADING_BOUNDARY = "### Boundary rules (auto-generated)"
SCENARIO_SECTION_HEADING = "### Scenario rules"

REFLECT_SYNTHESIS_HINT = (
    "Stop using tools. Output JSON only with schema: "
    '{"reasoning": "one sentence", "edits": [{"op": "append|replace|insert_after", '
    '"target": "section or empty", "content": "concrete skill rule"}]}. '
    "At least one edit must address the rollout failure checks."
)

CODE_TOOLS_REFLECT_HINT = """
## Code Reading Tools
File tools: search_code, read_code_file, list_code_files.
Graph tools (if available): explore_symbol, get_symbol_source, get_symbol_node, find_callers,
find_callees, list_graph_files, search_symbol, get_code_context, trace_symbol, impact_symbol,
graph_status.
Prefer explore_symbol / get_code_context on benchmark context_refs when present.
Use trace_symbol with to_symbol to verify call chains (returns paths_to.summary like A → B → C).
Ground skill edits in real code: cite file paths + symbol names from tool results.
After research, respond with JSON only (no more tool calls).
"""

REFLECT_SYSTEM_PROMPT = """You are a Skill document optimizer. The Skill guides a target agent on benchmark tasks.

## Available Sections (use as insert_after target)
{section_index}

## Step Buffer (DO NOT repeat rejected edits)
{step_buffer_summary}

## Instructions
1. Analyze missed verification checks — identify ROOT CAUSE per task type.
2. Propose 1-3 edits with op="insert_after", targeting the most relevant section.
3. Each edit: actionable bullet rules (>= 50 chars), covering missed checks semantically — not keyword dumps.
4. For primary-deliverable failures: add rules about required output format and verification tokens.
5. For clarify/reject/boundary failures: add rules for insufficient or invalid inputs — do not add primary-deliverable output rules.

## Code-Fact Grounding (design 09)
- Only propose business-mapping rules (account mapping, transaction type, amount direction) that are grounded in at least one Code Fact.
- If a failure has no Code Facts, propose an investigation step or scorer/format fix — do NOT convert missed_checks directly into skill text.
- Output format / prompt echo / alias-gap failures may be fixed without Code Facts.

FORBIDDEN: "# Verify", "need improvement", "TODO", keyword-only lists without actionable rules.

## Output
Return JSON only. Keep "reasoning" to ONE sentence (≤ 80 chars).
{{"reasoning": "...", "edits": [{{"op": "insert_after", "target": "### section", "content": "- rule...", "source_type": "failure"}}]}}"""

REFLECT_USER_PROMPT = """## Current Skill
{current_skill}

## Failure Cases
{failure_text}"""

REFLECT_PRIMARY_FOCUS = """

## Focus: primary deliverable failures
Target workflow/output sections. Add concrete format and content rules for missed verification checks.
When expected_checks require literal tokens, preserve exact spelling/casing in the proposed rules.
"""

REFLECT_BOUNDARY_FOCUS = """

## Focus: boundary / clarify / reject failures
Target constraint or incomplete-input sections. Require clarification or refusal per the skill.
Do not add primary-deliverable output rules when the task expects clarify/reject behavior.
"""

REFLECT_SUCCESS_PROMPT = """Based on successful cases, propose edits to preserve effective patterns.
Return JSON: {{"reasoning": "...", "edits": []}} If no changes needed, return empty edits array."""

SELECT_PROMPT = """## Task
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


def is_numeric_check(check: str) -> bool:
    """纯数字/金额类 check 不作为独立语义规则主题。"""
    return bool(re.fullmatch(r"[\d.,]+", check.strip()))


def is_graph_searchable_check(check: str) -> bool:
    """判断 missed check 是否适合作为代码图谱搜索词（通用启发式，无领域词表）。"""
    check = check.strip()
    if not check or is_numeric_check(check):
        return False
    # 含拉丁标识符的 token（如 Charge、INCOME_FROM_FEES）可用于符号/全文检索
    if re.search(r"[A-Za-z_]", check):
        return True
    # 较长自然语言 token 偶见于注释/文档
    return len(check) >= 5


def resolve_reflect_focus(result: dict) -> str:
    """从 rollout 结果判定 reflect 分组（primary vs boundary）。

    Benchmark item 可选字段：
    - ``response_mode``: ``clarify`` | ``reject`` | ``answer``（默认 answer → primary）
    - ``reflect_focus``: ``primary`` | ``boundary``（显式覆盖）
    """
    explicit = str(result.get("reflect_focus") or "").strip().lower()
    if explicit in (PRIMARY_FOCUS, BOUNDARY_FOCUS):
        return explicit
    mode = str(result.get("response_mode") or "answer").strip().lower()
    if mode in _BOUNDARY_RESPONSE_MODES:
        return BOUNDARY_FOCUS
    return PRIMARY_FOCUS


def split_failure_groups(failed: list[dict]) -> tuple[list[dict], list[dict]]:
    """将失败 case 分为 primary deliverable vs boundary/clarify。"""
    primary: list[dict] = []
    boundary: list[dict] = []
    for r in failed:
        if resolve_reflect_focus(r) == BOUNDARY_FOCUS:
            boundary.append(r)
        else:
            primary.append(r)
    return primary, boundary


def skill_section_index(skill: str) -> str:
    """提取 skill 章节标题，供 insert_after/replace 定位。"""
    headers = re.findall(r"^#{1,3}\s+.+", skill, re.M)
    if not headers:
        return "(no section headers found)"
    return "\n".join(f"- {h.strip()}" for h in headers[:20])


def _iter_skill_sections(skill: str) -> list[tuple[str, str]]:
    """按 ## / ### 标题切分 skill，返回 (heading, body) 列表。"""
    matches = list(re.finditer(r"^(#{1,3}\s+.+)$", skill, re.M))
    if not matches:
        return []
    sections: list[tuple[str, str]] = []
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(skill)
        heading = match.group(1).strip()
        body = skill[start:end].strip()
        sections.append((heading, body))
    return sections


def skill_compact_for_reflect(skill: str, max_chars: int = 1500) -> str:
    """Reflect 用精简 skill 摘要：优先约束/流程/输出类章节。"""
    sections = _iter_skill_sections(skill)
    if not sections:
        return skill[:max_chars]

    def _priority(heading: str) -> bool:
        lower = heading.lower()
        return any(k in lower for k in _SECTION_PRIORITY_KEYWORDS)

    picked = [s for s in sections if _priority(s[0])] or sections[-3:]
    chunks = [f"{h}\n{b[:500]}" for h, b in picked if b or h]
    compact = "\n\n---\n\n".join(chunks) if chunks else skill[:max_chars]
    return compact[:max_chars]


def find_insert_target(skill: str, focus: str = PRIMARY_FOCUS) -> str:
    """根据 focus 在 skill 中选择最佳插入章节标题。"""
    headers = [h.strip() for h in re.findall(r"^#{1,3}\s+.+", skill, re.M)]
    if not headers:
        return ""
    keywords = (
        _BOUNDARY_TARGET_KEYWORDS
        if focus == BOUNDARY_FOCUS
        else _PRIMARY_TARGET_KEYWORDS
    )
    for h in headers:
        lower = h.lower()
        if any(k in lower for k in keywords):
            return h
    return headers[-1]


def semantic_rule_for_check(check: str) -> str:
    """将 missed check 转为通用语义化规则。"""
    if is_numeric_check(check):
        return f"Output must include the verification token or value «{check}»"
    return f"Output must satisfy verification check «{check}»"


def build_reflect_focus_hint(focus: str) -> str:
    return REFLECT_PRIMARY_FOCUS if focus == PRIMARY_FOCUS else REFLECT_BOUNDARY_FOCUS


def reflect_stage_for_focus(focus: str) -> str:
    return (
        "reflect_failure_primary"
        if focus == PRIMARY_FOCUS
        else "reflect_failure_boundary"
    )


def rule_section_heading(focus: str) -> str:
    return (
        RULE_SECTION_HEADING_PRIMARY
        if focus == PRIMARY_FOCUS
        else RULE_SECTION_HEADING_BOUNDARY
    )


def summarize_step_buffer_for_reflect(
    step_buffer: list[dict] | None,
    rejected_edits: list | None = None,
) -> str:
    """构建 Reflect prompt 用的 step buffer 摘要（含 rejected buffer 落盘记录）。"""
    parts: list[str] = []

    if rejected_edits:
        parts.append("Previously REJECTED edits (do NOT propose these again):")
        for e in rejected_edits[-5:]:
            op = getattr(e, "op", "?")
            content = (getattr(e, "content", "") or "")[:80]
            parts.append(f"  - [{op}] {content}")
        parts.append("")

    if step_buffer:
        rejected_buffer_lines: list[str] = []
        failure_types: dict[str, int] = {}
        for buf in step_buffer:
            if not isinstance(buf, dict):
                continue
            buf_type = buf.get("type", "")
            if buf_type == "rejected_buffer":
                rec = buf.get("record") or {}
                reason = rec.get("reason", "gate_reject")
                content = (rec.get("content") or "")[:80]
                delta = rec.get("after_score", 0) - rec.get("before_score", 0)
                rejected_buffer_lines.append(
                    f"  - [{reason}] Δ={delta:+.3f} {content}"
                )
            elif buf_type == "rejected_edit":
                edit = buf.get("edit")
                if edit is not None:
                    op = getattr(edit, "op", "?")
                    content = (getattr(edit, "content", "") or "")[:80]
                    rejected_buffer_lines.append(f"  - [step_reject] [{op}] {content}")
            elif buf_type == "failure":
                ft = buf.get("failure_type", buf_type)
                failure_types[ft] = failure_types.get(ft, 0) + 1

        if rejected_buffer_lines:
            parts.append("Gate-rejected edits from buffer (do NOT repeat):")
            parts.extend(rejected_buffer_lines[-8:])
            parts.append("")

        if failure_types:
            parts.append("Previously observed failure patterns:")
            for ft, count in sorted(failure_types.items(), key=lambda x: -x[1]):
                parts.append(f"  - {ft}: {count} occurrences")
            parts.append("")

    if not parts:
        return "(no prior buffer information — this is the first step)"

    return "\n".join(parts)
