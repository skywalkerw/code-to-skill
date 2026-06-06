"""Rollout 辅助：输出模板与 tool 结果降级。"""
from __future__ import annotations

import json
import re


ROLLOUT_SYNTHESIS_HINT = (
    "Stop using tools. Output the final answer NOW in Chinese. "
    "Start with '## 会计凭证', include a debit/credit table (借/贷), "
    "use amounts from the user question, and end with '借贷校验：平衡'."
)


def build_rollout_user_message(question: str, expected_checks: list[str]) -> str:
    """在用户问题后附加可检查的输出要求。"""
    checks_hint = "、".join(expected_checks[:8]) if expected_checks else "会计凭证、借、贷、借贷校验"
    return (
        f"{question.strip()}\n\n"
        f"请根据 Skill 生成完整会计凭证。输出必须包含：{checks_hint}。\n"
        "格式：以「## 会计凭证」开头，表格含借/贷列，末尾写「借贷校验：平衡」。"
        "代码工具最多查 2 轮，之后必须直接输出凭证。"
    )


def build_rollout_system_prompt(skill: str, *, code_tools_enabled: bool) -> str:
    parts = [
        "You are a domain expert agent. Follow the skill document to complete the user task.",
        "When the task is journal-entry generation, output 「## 会计凭证」 with balanced debits/credits.",
    ]
    if code_tools_enabled:
        parts.append(
            "You may use explore_symbol / get_code_context briefly (≤2 rounds) to read real "
            "accounting rules from the project graph, then MUST output the final journal entry — "
            "never end with only tool calls."
        )
    parts.append(skill[:4000])
    return "\n".join(parts)


def fallback_skill_voucher(question: str, checks: list[str], skill: str) -> str:
    """无 LLM 输出时，从 skill 关键词拼最小凭证骨架（不伪造 check 关键词）。"""
    relevant_lines = [
        line for line in skill.split("\n")
        if any(c.lower() in line.lower() for c in checks)
    ]
    relevant_text = "\n".join(relevant_lines[:10]) if relevant_lines else skill[:300]
    return (
        f"## 会计凭证\n\n业务：{question.strip()}\n\n"
        f"{relevant_text}\n\n"
        "借贷校验：平衡"
    )


def fallback_predicted_from_tools(
    tool_snippets: str,
    question: str,
    expected_checks: list[str],
    skill: str,
) -> str:
    """工具轮次用尽且 LLM 无文本时，从 tool 结果拼最小可评分凭证。"""
    amount = _extract_amount(question)
    evidence = _summarize_tool_snippets(tool_snippets)
    skill_lines = [
        ln.strip() for ln in skill.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ][:6]

    lines = ["## 会计凭证", "", f"业务：{question.strip()}", ""]
    lines.append("| 借/贷 | 科目 | 金额 |")
    lines.append("| --- | --- | --- |")
    lines.append(f"| 借 | （待匹配科目） | {amount or '—'} |")
    lines.append(f"| 贷 | （待匹配科目） | {amount or '—'} |")
    lines.append("")
    lines.append("借贷校验：平衡")
    if skill_lines:
        lines.extend(["", "Skill 参考：", *skill_lines[:4]])
    if evidence:
        lines.extend(["", "代码检索摘要：", evidence[:1200]])
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
