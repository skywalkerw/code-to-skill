"""从 benchmark 失败 case + 代码图谱构建 reflect 用的代码证据。"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from code_to_skill.time_utils import local_timestamp

from .reflect_helpers import is_graph_searchable_check


@dataclass
class ReflectEvidenceMetrics:
    """Code Evidence 预取指标（reflect / inspect 可读）。"""

    total_refs: int = 0
    resolved_refs: int = 0
    evidence_hits: int = 0
    fallback_queries: int = 0
    irrelevant_or_empty_hits: int = 0
    precise_hits: int = 0
    role_hits: int = 0
    cases_with_evidence: int = 0
    cases_total: int = 0

    def merge(self, other: "ReflectEvidenceMetrics") -> None:
        for key in ReflectEvidenceMetrics.__dataclass_fields__:
            setattr(self, key, getattr(self, key) + getattr(other, key))

    def to_dict(self) -> dict[str, int]:
        return asdict(self)

    @property
    def ref_resolve_rate(self) -> float:
        return self.resolved_refs / max(self.total_refs, 1)

    @property
    def evidence_hit_rate(self) -> float:
        return self.evidence_hits / max(self.cases_total, 1)


@dataclass
class ReflectEvidenceResult:
    text: str
    metrics: ReflectEvidenceMetrics = field(default_factory=ReflectEvidenceMetrics)


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


def graph_queries_from_failure(failure: dict) -> list[str]:
    """从失败 rollout 推断通用图谱搜索词（无项目硬编码）。"""
    queries: list[str] = []
    question = (failure.get("question") or "").strip()

    for check in failure.get("missed_checks", []):
        check = (check or "").strip()
        if is_graph_searchable_check(check):
            queries.append(check)

    if question:
        queries.append(question[:120])

    queries.extend(extract_symbol_hints_from_question(question))

    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out[:6]


def trace_pairs_from_failure(failure: dict) -> list[tuple[str, str]]:
    """从 benchmark context_refs 推断 trace_symbol 的 (from, to) 符号对。"""
    pairs: list[tuple[str, str]] = []

    for ref in failure.get("context_refs") or []:
        path, symbol = parse_context_ref(ref)
        if not symbol:
            continue
        stem = os.path.splitext(os.path.basename(path))[0]
        if stem and stem != symbol:
            pairs.append((stem, symbol))

    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for a, b in pairs:
        key = (a.strip(), b.strip())
        if key[0] and key[1] and key not in seen:
            seen.add(key)
            out.append(key)
    return out[:4]


def _resolve_from_entry(
    sidecars: Any,
    *,
    file_path: str = "",
    symbol: str = "",
    item: dict | None = None,
) -> str:
    """用 entrypoints sidecar 解析 trace 起点（替代 api 路径启发式）。"""
    item = item or {}
    if sidecars is None or not getattr(sidecars, "use_entrypoints", True):
        return ""
    index = getattr(sidecars, "entrypoints", None)
    if index is None:
        return ""
    return index.resolve_from_entry(
        file_path=file_path,
        symbol=symbol,
        entrypoint_id=str(item.get("entrypoint_id") or ""),
    )


def _append_evidence_index_hits(
    sidecars: Any,
    result: dict,
    block_parts: list[str],
    metrics: ReflectEvidenceMetrics,
) -> bool:
    if sidecars is None or not getattr(sidecars, "use_evidence_index", True):
        return False
    store = getattr(sidecars, "evidence_index", None)
    if store is None:
        return False

    hits: list[Any] = []
    for ref in result.get("context_refs") or []:
        hits.extend(store.lookup_ref(ref))
    for atom_id in result.get("atom_ids") or []:
        hits.extend(store.lookup_atom(atom_id))

    seen: set[str] = set()
    added = False
    for hit in hits:
        if hit.evidence_id in seen:
            continue
        seen.add(hit.evidence_id)
        block_parts.append(store.format_hit(hit))
        metrics.precise_hits += 1
        metrics.evidence_hits += 1
        added = True
    return added


def _append_role_index_hits(
    sidecars: Any,
    result: dict,
    code_tools: Any,
    block_parts: list[str],
    metrics: ReflectEvidenceMetrics,
) -> bool:
    if sidecars is None or not getattr(sidecars, "use_role_index", True):
        return False
    index = getattr(sidecars, "role_index", None)
    if index is None:
        return False

    framework, role = sidecars.resolve_graph_role(result)
    if not role:
        return False

    entries = index.lookup(role, framework=framework, limit=3)
    added = False
    for entry in entries:
        block_parts.append(
            f"**Role[{entry.framework}/{entry.role}]** `{entry.file_path}`: "
            f"{', '.join(entry.symbols[:4])}"
        )
        metrics.role_hits += 1
        metrics.evidence_hits += 1
        added = True
        if entry.symbols:
            raw = code_tools.execute({
                "function": {
                    "name": "explore_symbol",
                    "arguments": json.dumps({
                        "symbol": entry.symbols[0],
                        "include_source": True,
                    }),
                },
            })
            data = json.loads(raw)
            if not data.get("error") and data.get("source"):
                block_parts.append(f"```\n{data['source'][:600]}\n```")
                metrics.resolved_refs += 1
    return added


def build_reflect_code_evidence(
    failed_results: list[dict],
    code_tools: Any,
    *,
    max_cases: int = 5,
    max_chars: int = 4500,
    sidecars: Any = None,
) -> ReflectEvidenceResult:
    """为 reflect 预取目标项目真实代码证据（减少空工具轮次）。"""
    metrics = ReflectEvidenceMetrics(cases_total=min(len(failed_results), max_cases))
    if code_tools is None or not getattr(code_tools, "graph_enabled", False):
        return ReflectEvidenceResult("", metrics)

    sections: list[str] = []
    used_chars = 0

    for result in failed_results[:max_cases]:
        case_id = result.get("id", "")
        question = (result.get("question") or "")[:120]
        missed = result.get("missed_checks", [])[:6]
        refs = list(result.get("context_refs") or [])
        metrics.total_refs += len(refs[:2])

        block_parts = [f"### Case {case_id}: {question}"]
        if missed:
            block_parts.append(f"missed checks: {', '.join(missed)}")

        parts_before_refs = len(block_parts)
        _append_evidence_index_hits(sidecars, result, block_parts, metrics)
        if len(block_parts) <= parts_before_refs:
            _append_role_index_hits(sidecars, result, code_tools, block_parts, metrics)

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
                    metrics.resolved_refs += 1
                    metrics.evidence_hits += 1
                    block_parts.append(
                        _format_explore_card(data, hint=file_path)
                    )
                    stem = os.path.splitext(os.path.basename(file_path))[0] if file_path else ""
                    from_entry = _resolve_from_entry(
                        sidecars,
                        file_path=file_path or data.get("file_path", ""),
                        symbol=symbol_hint,
                        item=result,
                    )
                    chain = _fetch_trace_summary(
                        code_tools,
                        from_symbol=stem or data.get("name", symbol_hint),
                        to_symbol=data.get("name", symbol_hint),
                        from_entry=from_entry,
                    )
                    if chain:
                        block_parts.append(chain)
                    continue
                metrics.irrelevant_or_empty_hits += 1
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
                    metrics.resolved_refs += 1
                    metrics.evidence_hits += 1
                    block_parts.append(
                        f"**File** `{file_path}` (L1-{data.get('end_line', '?')}):\n"
                        f"```\n{data['content'][:1200]}\n```"
                    )
                else:
                    metrics.irrelevant_or_empty_hits += 1

        if len(block_parts) <= parts_before_refs:
            for gq in graph_queries_from_failure(result):
                metrics.fallback_queries += 1
                raw = code_tools.execute({
                    "function": {
                        "name": "get_code_context",
                        "arguments": json.dumps({"query": gq, "max_blocks": 2}),
                    },
                })
                data = json.loads(raw)
                blocks = data.get("blocks", [])[:1]
                if blocks:
                    metrics.evidence_hits += 1
                    for blk in blocks:
                        block_parts.append(
                            f"**Graph[{gq}]** `{blk.get('symbol')}` @ {blk.get('file_path')}:\n"
                            f"```\n{(blk.get('content') or '')[:800]}\n```"
                        )
                else:
                    metrics.irrelevant_or_empty_hits += 1
                if len(block_parts) > parts_before_refs:
                    break

        if len(block_parts) <= parts_before_refs + 1:
            from_entry = _resolve_from_entry(
                sidecars,
                file_path=(result.get("context_refs") or [""])[0].split("#")[0],
                item=result,
            )
            for from_sym, to_sym in trace_pairs_from_failure(result):
                metrics.fallback_queries += 1
                chain = _fetch_trace_summary(
                    code_tools,
                    from_symbol=from_sym,
                    to_symbol=to_sym,
                    from_entry=from_entry,
                )
                if chain:
                    metrics.evidence_hits += 1
                    block_parts.append(chain)
                    break
                metrics.irrelevant_or_empty_hits += 1

        if len(block_parts) <= parts_before_refs and missed:
            query = " ".join(missed[:4])
            metrics.fallback_queries += 1
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
            blocks = data.get("blocks", [])[:2]
            if blocks:
                metrics.evidence_hits += 1
                for blk in blocks:
                    block_parts.append(
                        f"**Graph** `{blk.get('symbol')}` @ {blk.get('file_path')}:\n"
                        f"```\n{(blk.get('content') or '')[:800]}\n```"
                    )
            else:
                metrics.irrelevant_or_empty_hits += 1

        if len(block_parts) > parts_before_refs:
            metrics.cases_with_evidence += 1

        block = "\n".join(block_parts)
        if used_chars + len(block) > max_chars:
            break
        sections.append(block)
        used_chars += len(block)

    if not sections:
        return ReflectEvidenceResult("", metrics)
    text = "## Code Evidence (from project graph)\n\n" + "\n\n".join(sections)
    return ReflectEvidenceResult(text, metrics)


def validate_context_refs_for_items(
    items: list[dict],
    code_tools: Any | None = None,
    *,
    repo_root: str = "",
) -> dict[str, Any]:
    """解析 benchmark context_refs，输出 ``context_ref_report.json`` 载荷。"""
    entries: list[dict[str, Any]] = []
    total_refs = 0
    resolved = 0
    file_hits = 0
    symbol_hits = 0
    misses = 0

    graph_enabled = (
        code_tools is not None
        and getattr(code_tools, "graph_enabled", False)
    )

    for item in items:
        item_id = item.get("id", "")
        refs = list(item.get("context_refs") or [])
        item_entry: dict[str, Any] = {"id": item_id, "refs": []}

        for ref in refs:
            total_refs += 1
            file_path, symbol_hint = parse_context_ref(ref)
            status = "miss"
            detail = ""

            if symbol_hint and graph_enabled:
                raw = code_tools.execute({
                    "function": {
                        "name": "explore_symbol",
                        "arguments": json.dumps({
                            "symbol": symbol_hint,
                            "include_source": False,
                        }),
                    },
                })
                data = json.loads(raw)
                if not data.get("error"):
                    status = "symbol_hit"
                    symbol_hits += 1
                    resolved += 1
                    detail = data.get("file_path", "")
                elif file_path:
                    status, detail = _probe_file_ref(file_path, code_tools, repo_root)
                    if status == "file_hit":
                        file_hits += 1
                        resolved += 1
                    else:
                        misses += 1
                else:
                    misses += 1
            elif file_path:
                status, detail = _probe_file_ref(file_path, code_tools, repo_root)
                if status == "file_hit":
                    file_hits += 1
                    resolved += 1
                else:
                    misses += 1
            else:
                misses += 1

            item_entry["refs"].append({
                "ref": ref,
                "file_path": file_path,
                "symbol": symbol_hint,
                "status": status,
                "detail": detail,
            })

        entries.append(item_entry)

    return {
        "schema_version": "1.0",
        "generated_at": local_timestamp(),
        "summary": {
            "items": len(items),
            "total_refs": total_refs,
            "resolved": resolved,
            "file_hits": file_hits,
            "symbol_hits": symbol_hits,
            "misses": misses,
            "resolve_rate": round(resolved / max(total_refs, 1), 4),
            "graph_enabled": graph_enabled,
        },
        "items": entries,
    }


def _probe_file_ref(
    file_path: str,
    code_tools: Any | None,
    repo_root: str,
) -> tuple[str, str]:
    if code_tools is not None and getattr(code_tools, "enabled", False):
        raw = code_tools.execute({
            "function": {
                "name": "read_code_file",
                "arguments": json.dumps({"path": file_path, "end_line": 5}),
            },
        })
        data = json.loads(raw)
        if data.get("content"):
            return "file_hit", file_path
    if repo_root:
        abs_path = file_path if os.path.isabs(file_path) else os.path.join(repo_root, file_path)
        if os.path.isfile(abs_path):
            return "file_hit", abs_path
    return "miss", ""


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


def build_rollout_item_context(
    item: dict,
    code_tools: Any,
    *,
    max_chars: int = 1800,
    sidecars: Any = None,
) -> str:
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
    if sidecars and getattr(sidecars, "evidence_index", None):
        store = sidecars.evidence_index
        for ref in refs[:2]:
            for hit in store.lookup_ref(ref):
                parts.append(store.format_hit(hit))
        for atom_id in item.get("source_atom_ids") or []:
            for hit in store.lookup_atom(atom_id):
                parts.append(store.format_hit(hit))

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
                    f"[code ref {symbol_hint} @ {data.get('file_path', file_path)}]\n"
                    f"{data['source'][:max_chars // 2]}"
                )
                stem = os.path.splitext(os.path.basename(file_path))[0]
                from_entry = _resolve_from_entry(
                    sidecars,
                    file_path=file_path or data.get("file_path", ""),
                    symbol=symbol_hint,
                    item=item,
                )
                chain = _fetch_trace_summary(
                    code_tools,
                    from_symbol=stem or symbol_hint,
                    to_symbol=symbol_hint,
                    from_entry=from_entry,
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
                    f"[file {file_path}]\n{data['content'][:max_chars // 2]}"
                )
            else:
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
                                f"[graph {top.get('name')} @ {top.get('file_path')}]\n"
                                f"{ex['source'][:max_chars // 2]}"
                            )

    if not parts:
        return ""
    body = "\n\n".join(parts)
    return f"\n\n--- Project code reference (consult before final answer) ---\n{body[:max_chars]}\n"


def extract_symbol_hints_from_question(question: str) -> list[str]:
    """从问题文本提取 CamelCase 符号提示。"""
    return re.findall(r"\b([A-Z][a-z]+(?:[A-Z][a-z]*)+)\b", question)[:4]
