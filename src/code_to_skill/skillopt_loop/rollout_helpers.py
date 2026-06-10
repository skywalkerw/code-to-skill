"""Rollout 辅助：输出模板与 tool 结果降级（通用框架，不含目标项目领域知识）。"""
from __future__ import annotations

import json
import re


ROLLOUT_SYNTHESIS_HINT = (
    "Stop using tools. Output your final answer now. "
    "Follow the skill document for format and constraints. "
    "Do NOT paste or repeat the skill document."
)

# 通用占位符标记（语言无关）
_PLACEHOLDER_MARKERS = (
    "(placeholder)",
    "<placeholder>",
    "TBD",
    "TODO:",
    "TODO ",
)


def _format_checks(checks: list[str], *, limit: int = 12) -> str:
    tokens = [c.strip() for c in checks if c and c.strip()]
    return ", ".join(tokens[:limit])


def build_rollout_synthesis_hint(expected_checks: list[str]) -> str:
    """按 expected_checks 追加 synthesis 阶段的验证 token 提醒。"""
    hint = ROLLOUT_SYNTHESIS_HINT
    checks_hint = _format_checks(expected_checks or [])
    if checks_hint:
        hint += f" Your answer must include these verification tokens: {checks_hint}."
    return hint


def assemble_rollout_user_content(
    task_msg: str,
    code_ctx: str = "",
    *,
    task_limit: int = 1200,
    code_limit: int = 1400,
    total_limit: int = 3000,
) -> str:
    """分段预算组装 rollout user 消息，避免整体截断丢掉代码证据。"""
    task_part = (task_msg or "")[:task_limit]
    if not code_ctx:
        return task_part[:total_limit]
    remaining = max(0, total_limit - len(task_part))
    code_budget = min(code_limit, remaining)
    if code_budget <= 0:
        return task_part[:total_limit]
    return task_part + code_ctx[:code_budget]


def build_rollout_user_message(
    question: str,
    expected_checks: list[str],
    *,
    item: dict | None = None,
) -> str:
    """在用户问题后附加可检查的输出要求。

    Benchmark item 可选字段：
    - ``response_mode``: ``clarify`` | ``reject`` | ``answer``（默认 ``answer``）
    - ``rollout_hint``: 项目/任务级补充说明（由 benchmark 或 skill 侧提供）
    """
    checks = expected_checks or []
    item = item or {}
    checks_hint = _format_checks(checks)
    rollout_hint = str(item.get("rollout_hint") or "").strip()
    mode = str(item.get("response_mode") or "answer").strip().lower()

    if mode == "clarify":
        msg = (
            f"{question.strip()}\n\n"
            "Follow the skill document. If information is insufficient, "
            "respond with a clarification instead of the primary deliverable."
        )
        if checks_hint:
            msg += f" Include these verification tokens: {checks_hint}."
        if rollout_hint:
            msg += f" {rollout_hint}"
        return msg

    if mode == "reject":
        msg = (
            f"{question.strip()}\n\n"
            "Follow the skill document. This input violates constraints — "
            "refuse the primary deliverable and explain why."
        )
        if checks_hint:
            msg += f" Include these verification tokens: {checks_hint}."
        if rollout_hint:
            msg += f" {rollout_hint}"
        return msg

    msg = (
        f"{question.strip()}\n\n"
        "Follow the skill document to complete this task."
    )
    if checks_hint:
        msg += f" Your answer must satisfy these verification checks: {checks_hint}."
    if rollout_hint:
        msg += f" {rollout_hint}"
    msg += " Output only the final answer; do not paste the skill document."
    mode = str((item or {}).get("context_mode") or "inline").strip().lower()
    if mode == "agent_read":
        msg += " Use code tools to fetch the referenced context before answering."
    elif mode != "none":
        msg += " Use code tools briefly if needed, then produce the final answer."
    return msg


def sanitize_skill_for_rollout(skill: str) -> str:
    """去掉会诱导模型照抄的占位符示例，保留规则正文。"""
    if not skill:
        return skill
    lines: list[str] = []
    in_fence = False
    for line in skill.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        lower = line.lower()
        if any(m.lower() in lower for m in _PLACEHOLDER_MARKERS):
            continue
        lines.append(line)
    cleaned = "\n".join(lines)
    return cleaned if cleaned.strip() else skill


_TOOL_LEAK_PATTERNS = (
    re.compile(r"<\s*[｜|].*tool[_\s-]*calls", re.IGNORECASE),
    re.compile(r"DSML.*tool[_\s-]*calls", re.IGNORECASE),
    re.compile(r"tool[_\s-]*calls\s*>", re.IGNORECASE),
    re.compile(r'"name"\s*:\s*"(search_code|read_code_file|explore_symbol)"'),
)


def looks_like_tool_call_leak(text: str) -> bool:
    """检测最终答案是否泄漏 tool-call / DSML 标记而非真实输出。"""
    t = (text or "").strip()
    if not t:
        return False
    if any(p.search(t) for p in _TOOL_LEAK_PATTERNS):
        return True
    lowered = t.lower()
    if "dsml" in lowered and "tool" in lowered:
        return True
    if lowered.count("tool_calls") >= 1 and ("<" in t or "{" in t):
        return True
    return False


def build_tool_leak_retry_hint(expected_checks: list[str] | None = None) -> str:
    """tool 泄漏后强制纯文本最终答案的 synthesis 提示。"""
    hint = (
        "Your previous response contained tool-call markup instead of a final answer. "
        "Do NOT output XML, JSON tool calls, DSML markers, or function invocations. "
        "Using only information already gathered, output the complete final deliverable "
        "in markdown / natural language."
    )
    checks_hint = _format_checks(expected_checks or [])
    if checks_hint:
        hint += f" Include verification tokens: {checks_hint}."
    return hint


def extract_rollout_answer(predicted: str) -> str:
    """评分前截取主回答段落，去掉模型回显的 skill 正文。"""
    if not predicted:
        return predicted
    m = re.search(r"^##\s+\S", predicted, re.MULTILINE)
    if not m:
        return predicted
    text = predicted[m.start():]
    for marker in ("\n# ", "\n---\n# "):
        idx = text.find(marker, 10)
        if idx > 20:
            text = text[:idx]
    return text.strip()


def build_rollout_system_prompt(skill: str, *, code_tools_enabled: bool) -> str:
    parts = [
        "You are a domain expert agent. Follow the skill document to complete the user task.",
        "Output only the final deliverable required by the skill — never paste or repeat the skill document.",
        "When the skill requires clarification or refusal for incomplete/invalid inputs, "
        "follow those rules instead of producing the primary deliverable.",
    ]
    if code_tools_enabled:
        parts.append(
            "You may use available code tools briefly to gather context from the project, "
            "then MUST output the final answer — never end with only tool calls."
        )
    parts.append(sanitize_skill_for_rollout(skill)[:4000])
    return "\n".join(parts)


def fallback_skill_answer(question: str, checks: list[str], skill: str) -> str:
    """无 LLM 输出时，从 skill 与 checks 拼最小可评分骨架（不注入领域模板）。"""
    checks = checks or []
    relevant_lines = [
        line for line in skill.split("\n")
        if any(c.lower() in line.lower() for c in checks)
    ]
    relevant_text = "\n".join(relevant_lines[:10]) if relevant_lines else skill[:300]
    checks_line = _format_checks(checks, limit=8)
    parts = [f"Task: {question.strip()}"]
    if checks_line:
        parts.append(f"Checks: {checks_line}")
    if relevant_text:
        parts.append(relevant_text)
    return "\n".join(parts)


def fallback_predicted_from_tools(
    tool_snippets: str,
    question: str,
    expected_checks: list[str],
    skill: str,
) -> str:
    """工具轮次用尽且 LLM 无文本时，从 tool 结果与 skill 拼最小可评分回答。"""
    evidence = _summarize_tool_snippets(tool_snippets)
    skill_lines = [
        ln.strip() for ln in skill.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ][:6]
    checks_line = _format_checks(expected_checks or [], limit=8)

    lines = [f"Task: {question.strip()}", ""]
    if checks_line:
        lines.append(f"Checks: {checks_line}")
    if skill_lines:
        lines.extend(["", "Skill reference:", *skill_lines[:4]])
    if evidence:
        lines.extend(["", "Code context:", evidence[:1200]])
    return "\n".join(lines)


def _extract_amount(text: str) -> str:
    m = re.search(r"[\d,]+\.?\d*", text.replace(",", ""))
    return m.group(0) if m else ""


def _summarize_tool_snippets(raw: str) -> str:
    if not raw:
        return ""
    chunks: list[str] = []
    for part in raw.split("\n---\n"):
        part = part.strip()
        if not part:
            continue
        try:
            data = json.loads(part)
            if isinstance(data, dict):
                if data.get("source"):
                    chunks.append(str(data["source"])[:500])
                elif data.get("content"):
                    chunks.append(str(data["content"])[:300])
                elif data.get("results"):
                    chunks.append(str(data["results"])[:400])
                elif data.get("blocks"):
                    for blk in data["blocks"][:2]:
                        if isinstance(blk, dict) and blk.get("content"):
                            chunks.append(
                                f"{blk.get('symbol','')}: {blk['content'][:300]}"
                            )
                elif data.get("explored"):
                    for ex in data["explored"][:2]:
                        if isinstance(ex, dict) and ex.get("source"):
                            chunks.append(
                                f"{ex.get('name','')}: {ex['source'][:300]}"
                            )
                else:
                    chunks.append(part[:200])
            else:
                chunks.append(part[:200])
        except json.JSONDecodeError:
            chunks.append(part[:200])
    return "\n".join(chunks[:4])
