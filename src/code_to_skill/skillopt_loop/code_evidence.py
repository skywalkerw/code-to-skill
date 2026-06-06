"""从 benchmark 失败 case + 代码图谱构建 reflect 用的代码证据。"""
from __future__ import annotations

import json
import re
from typing import Any


def parse_context_ref(ref: str) -> tuple[str, str]:
    """解析 context_ref：path/to/File.java#methodName。"""
    ref = (ref or "").strip()
    if "#" in ref:
        path, symbol = ref.rsplit("#", 1)
        return path.strip(), symbol.strip()
    if "::" in ref:
        path, symbol = ref.rsplit("::", 1)
        return path.strip(), symbol.strip()
    return ref, ""


def build_reflect_code_evidence(
    failed_results: list[dict],
    code_tools: Any,
    *,
    max_cases: int = 5,
    max_chars: int = 4500,
) -> str:
    """为 reflect 预取目标项目真实代码证据（减少空工具轮次）。"""
    if code_tools is None or not getattr(code_tools, "graph_enabled", False):
        return ""

    sections: list[str] = []
    used_chars = 0

    for result in failed_results[:max_cases]:
        case_id = result.get("id", "")
        question = (result.get("question") or "")[:120]
        missed = result.get("missed_checks", [])[:6]
        refs = list(result.get("context_refs") or [])

        block_parts = [f"### Case {case_id}: {question}"]
        if missed:
            block_parts.append(f"missed checks: {', '.join(missed)}")

        for ref in refs[:2]:
            file_path, symbol_hint = parse_context_ref(ref)
            if symbol_hint:
                raw = code_tools.execute({
                    "function": {
                        "name": "explore_symbol",
                        "arguments": json.dumps({
                            "symbol": symbol_hint,
                            "include_source": True,
                        }),
                    },
                })
                data = json.loads(raw)
                if not data.get("error"):
                    block_parts.append(
                        _format_explore_card(data, hint=file_path)
                    )
                    import os
                    stem = os.path.splitext(os.path.basename(file_path))[0] if file_path else ""
                    chain = _fetch_trace_summary(
                        code_tools,
                        from_symbol=stem or data.get("name", symbol_hint),
                        to_symbol=data.get("name", symbol_hint),
                        from_entry="rest" if "api" in (file_path or "").lower() else "",
                    )
                    if chain:
                        block_parts.append(chain)
                    continue
            if file_path:
                raw = code_tools.execute({
                    "function": {
                        "name": "read_code_file",
                        "arguments": json.dumps({
                            "path": file_path,
                            "end_line": 80,
                        }),
                    },
                })
                data = json.loads(raw)
                if data.get("content"):
                    block_parts.append(
                        f"**File** `{file_path}` (L1-{data.get('end_line', '?')}):\n"
                        f"```\n{data['content'][:1200]}\n```"
                    )

        if len(block_parts) <= 2:
            from .accounting_linker import graph_queries_for_failure

            for gq in graph_queries_for_failure(result):
                raw = code_tools.execute({
                    "function": {
                        "name": "get_code_context",
                        "arguments": json.dumps({"query": gq, "max_blocks": 2}),
                    },
                })
                data = json.loads(raw)
                for blk in data.get("blocks", [])[:1]:
                    block_parts.append(
                        f"**图谱[{gq}]** `{blk.get('symbol')}` @ {blk.get('file_path')}:\n"
                        f"```\n{(blk.get('content') or '')[:800]}\n```"
                    )
                if len(block_parts) > 2:
                    break

        if len(block_parts) <= 3:
            from .accounting_linker import trace_pairs_for_failure

            for from_sym, to_sym in trace_pairs_for_failure(result):
                chain = _fetch_trace_summary(
                    code_tools, from_symbol=from_sym, to_symbol=to_sym,
                )
                if chain:
                    block_parts.append(chain)
                    break

        if len(block_parts) <= 2 and missed:
            query = " ".join(missed[:4])
            raw = code_tools.execute({
                "function": {
                    "name": "get_code_context",
                    "arguments": json.dumps({
                        "query": query,
                        "max_blocks": 2,
                    }),
                },
            })
            data = json.loads(raw)
            for blk in data.get("blocks", [])[:2]:
                block_parts.append(
                    f"**Graph** `{blk.get('symbol')}` @ {blk.get('file_path')}:\n"
                    f"```\n{(blk.get('content') or '')[:800]}\n```"
                )

        block = "\n".join(block_parts)
        if used_chars + len(block) > max_chars:
            break
        sections.append(block)
        used_chars += len(block)

    if not sections:
        return ""
    return "## Code Evidence (from project graph)\n\n" + "\n\n".join(sections)


def _fetch_trace_summary(
    code_tools: Any,
    *,
    from_symbol: str,
    to_symbol: str,
    from_entry: str = "",
) -> str:
    """调用 trace_symbol 并格式化为简短调用链文本。"""
    if not from_symbol or not to_symbol:
        return ""
    args: dict[str, Any] = {
        "symbol": from_symbol,
        "to_symbol": to_symbol,
        "direction": "callees",
        "depth": 2,
        "path_max_depth": 10,
    }
    if from_entry:
        args["from_entry"] = from_entry
    raw = code_tools.execute({
        "function": {
            "name": "trace_symbol",
            "arguments": json.dumps(args),
        },
    })
    data = json.loads(raw)
    paths = data.get("paths_to") or []
    if paths:
        summaries = [p.get("summary", "") for p in paths[:2] if p.get("summary")]
        if summaries:
            return "**Call chain**: " + " | ".join(summaries)
    err = data.get("paths_to_error", "")
    if err and data.get("callees"):
        names = [c.get("name", "") for c in data["callees"][:4]]
        return f"**Nearby callees** of `{from_symbol}`: {', '.join(n for n in names if n)}"
    return ""


def _format_explore_card(data: dict[str, Any], hint: str = "") -> str:
    lines = [
        f"**Symbol** `{data.get('qualified_name') or data.get('name')}` "
        f"({data.get('kind')}) @ `{data.get('file_path')}`:"
    ]
    if hint:
        lines[0] += f" ref={hint}"
    if data.get("signature"):
        lines.append(f"signature: `{data['signature'][:200]}`")
    callers = data.get("callers") or []
    callees = data.get("callees") or []
    if callers:
        lines.append("callers: " + ", ".join(c["name"] for c in callers[:5]))
    if callees:
        lines.append("callees: " + ", ".join(c["name"] for c in callees[:5]))
    src = (data.get("source") or "").strip()
    if src:
        lines.append(f"```\n{src[:1500]}\n```")
    return "\n".join(lines)


def build_rollout_item_context(item: dict, code_tools: Any, *, max_chars: int = 1800) -> str:
    """为单条 rollout 预取 benchmark context_refs 对应的真实代码片段。"""
    if code_tools is None or not getattr(code_tools, "graph_enabled", False):
        return ""

    refs = list(item.get("context_refs") or [])
    if not refs:
        hints = extract_symbol_hints_from_question(item.get("question", ""))
        if not hints:
            return ""
        refs = [hints[0]]

    parts: list[str] = []
    for ref in refs[:2]:
        file_path, symbol_hint = parse_context_ref(ref)
        if symbol_hint:
            raw = code_tools.execute({
                "function": {
                    "name": "explore_symbol",
                    "arguments": json.dumps({"symbol": symbol_hint, "include_source": True}),
                },
            })
            data = json.loads(raw)
            if not data.get("error") and data.get("source"):
                chunk = (
                    f"[代码参考 {symbol_hint} @ {data.get('file_path', file_path)}]\n"
                    f"{data['source'][:max_chars // 2]}"
                )
                import os
                stem = os.path.splitext(os.path.basename(file_path))[0]
                chain = _fetch_trace_summary(
                    code_tools,
                    from_symbol=stem or symbol_hint,
                    to_symbol=symbol_hint,
                    from_entry="rest" if "api" in file_path.lower() else "",
                )
                if chain:
                    chunk += f"\n[{chain}]"
                parts.append(chunk)
                continue
        if file_path:
            raw = code_tools.execute({
                "function": {
                    "name": "read_code_file",
                    "arguments": json.dumps({"path": file_path, "end_line": 60}),
                },
            })
            data = json.loads(raw)
            if data.get("content"):
                parts.append(
                    f"[文件 {file_path}]\n{data['content'][:max_chars // 2]}"
                )
            else:
                import os

                stem = os.path.splitext(os.path.basename(file_path))[0]
                if stem:
                    raw = code_tools.execute({
                        "function": {
                            "name": "search_symbol",
                            "arguments": json.dumps({"query": stem, "max_results": 3}),
                        },
                    })
                    hits = json.loads(raw).get("results", [])
                    if hits:
                        top = hits[0]
                        explore_raw = code_tools.execute({
                            "function": {
                                "name": "explore_symbol",
                                "arguments": json.dumps({
                                    "symbol": top.get("name", stem),
                                    "include_source": True,
                                }),
                            },
                        })
                        ex = json.loads(explore_raw)
                        if ex.get("source"):
                            parts.append(
                                f"[图谱 {top.get('name')} @ {top.get('file_path')}]\n"
                                f"{ex['source'][:max_chars // 2]}"
                            )

    if not parts:
        return ""
    body = "\n\n".join(parts)
    return f"\n\n--- 项目代码参考（生成凭证前可对照） ---\n{body[:max_chars]}\n"


def extract_symbol_hints_from_question(question: str) -> list[str]:
    """从问题文本提取 CamelCase 符号提示。"""
    return re.findall(r"\b([A-Z][a-z]+(?:[A-Z][a-z]*)+)\b", question)[:4]
