"""Code-First Retrieval Pipeline（设计 09）。

确定性查询计划 → 多路召回 → 角色感知 rerank → CodeFact 提取。

纯工具层：不依赖 SkillOpt、benchmark、gate、rule bank。
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from hashlib import sha1
from typing import Any

from code_to_skill.time_utils import local_timestamp

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 通用业务词与输出格式词区分启发式
# ---------------------------------------------------------------------------

_GENERIC_OUTPUT_WORDS = frozenset({
    # 纯格式/结构词 — 任何领域通用的输出格式要求，不应作为代码搜索词
    # 注意：不包含可能同时是业务概念的词（如 total, summary）
    "表格", "表头", "列表", "缩进", "编号",
    "输出格式",  # 泛指的格式指令
    "markdown", "table", "header", "footer", "format", "output",
    "indentation", "numbered",
    "bullet", "heading", "column", "row",
})

# 通用提示词 / skill 指令相关词（不作为代码搜索词）
_GENERIC_PROMPT_WORDS = frozenset({
    "skill", "rule", "output", "format", "task", "question",
    "benchmark", "rollout", "agent", "model", "answer",
    "verify", "ensure", "check", "validate", "require",
    "include", "follow", "document", "instruction", "hint",
    "deliverable", "response", "expected",
})

# 可搜索内容的词特征 — 排除纯格式词和指令词后，剩下的词都是潜在搜索词
_CONTENT_WORD_MIN_LEN = 3
# 常见英文停用词 — 不适合作为代码搜索词
_ENGLISH_STOP_WORDS = frozenset({
    "the", "and", "for", "are", "but", "not", "you", "all",
    "can", "had", "her", "was", "one", "our", "out", "has",
    "have", "this", "that", "with", "from", "they", "will",
    "been", "were", "some", "what", "when", "make", "like",
    "just", "into", "over", "such", "each", "also", "how",
    "its", "after", "most", "very", "get", "set",
})
# 代码结构相关词 — 这些在其他项目也是通用的搜索线索，但不应成为业务匹配的主依据
_CODE_STRUCTURE_WORD_PATTERN = re.compile(
    r"\b(processor|service|domain|dto|enum|util|helper|"
    r"validator|mapper|event|listener|hook|"
    r"handler|controller|resource|config|repository)\b",
    re.IGNORECASE,
)
# CamelCase 符号提示模式
_CAMEL_CASE_PATTERN = re.compile(r"\b([A-Z][a-zA-Z0-9]{2,}(?:\.[A-Z][a-zA-Z0-9]+)*)\b")
# 中文词模式
_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]+")

# 评分器诊断词 — 这些词由 scorer/check 注入，对代码搜索价值低
# 它们不是业务代码内容，而是 scorer 用来检查输出格式/内容的元词
_SCORER_DIAGNOSTIC_TERMS: dict[str, bool] = {
    # 会计凭证 scorer 检查词
    "会计凭证": True, "借": True, "贷": True, "借贷校验": True,
    "借贷": True, "借方": True, "贷方": True, "借贷平衡": True,
    "余额": True, "库存": True, "银行": True, "现金": True,
    "会计": True, "凭证": True, "分录": True,
    # 通用格式检查词
    "verify": True, "ensure": True, "confirm": True, "check": True,
    "must": True, "should": True, "output": True, "format": True,
    "include": True, "return": True, "table": True, "markdown": True,
}

# ---------------------------------------------------------------------------
# 角色分类
# ---------------------------------------------------------------------------

_BUSINESS_ROLES = frozenset({
    "processor", "service", "domain", "dto", "enum", "helper", "util",
    "validator", "mapper", "event", "listener", "hook",
})

_GLUE_ROLES = frozenset({
    "handler_only", "swagger", "configuration", "starter",
    "controller", "resource_api", "api_resource",
    "rest_controller", "repository", "config",
})

# 路径关键词 → 角色启发式
_PATH_ROLE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?:^|/)processor/", re.I), "processor"),
    (re.compile(r"(?:^|/)service/", re.I), "service"),
    (re.compile(r"(?:^|/)domain/", re.I), "domain"),
    (re.compile(r"(?:^|/)dto/", re.I), "dto"),
    (re.compile(r"(?:^|/)enums?/", re.I), "enum"),
    (re.compile(r"(?:^|/)helper/", re.I), "helper"),
    (re.compile(r"(?:^|/)util/", re.I), "util"),
    (re.compile(r"(?:^|/)handler/", re.I), "handler_only"),
    (re.compile(r"(?:^|/)controller/", re.I), "handler_only"),
    (re.compile(r"(?:^|/)resource/", re.I), "resource_api"),
    (re.compile(r"(?:^|/)config/", re.I), "configuration"),
    (re.compile(r"(?:^|/)configuration/", re.I), "configuration"),
]

# 文件名/类名后缀 → 角色启发式
_CLASS_ROLE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"Processor(?:Impl)?$", re.I), "processor"),
    (re.compile(r"Service(?:Impl)?$", re.I), "service"),
    (re.compile(r"Handler$", re.I), "handler_only"),
    (re.compile(r"CommandHandler$", re.I), "handler_only"),
    (re.compile(r"ApiResource(?:Swagger)?$", re.I), "swagger"),
    (re.compile(r"Resource$", re.I), "resource_api"),
    (re.compile(r"Controller$", re.I), "handler_only"),
    (re.compile(r"Config(?:uration)?$", re.I), "configuration"),
    (re.compile(r"DTO$", re.I), "dto"),
    (re.compile(r"Enum$", re.I), "enum"),
    (re.compile(r"Constants?$", re.I), "enum"),
    (re.compile(r"Domain(?:Service)?$", re.I), "domain"),
    (re.compile(r"Validator$", re.I), "validator"),
    (re.compile(r"Mapper$", re.I), "mapper"),
    (re.compile(r"Builder$", re.I), "domain"),
    (re.compile(r"Listener$", re.I), "listener"),
    (re.compile(r"Event$", re.I), "event"),
    (re.compile(r"Helper$", re.I), "helper"),
    (re.compile(r"Utils?$", re.I), "util"),
    (re.compile(r"Starter$", re.I), "starter"),
]


def _classify_code_role(path: str, symbol: str = "", kind: str = "") -> str:
    """从文件路径和符号名推断代码角色。"""
    p = (path or "").replace(os.sep, "/")

    # 路径启发式
    for pattern, role in _PATH_ROLE_PATTERNS:
        if pattern.search(p):
            return role

    # 类名/符号名启发式
    for pattern, role in _CLASS_ROLE_PATTERNS:
        if symbol and pattern.search(symbol):
            return role
        if pattern.search(os.path.basename(p)):
            return role

    # kind 回退
    if kind in ("interface", "class", "enum"):
        parent = os.path.basename(os.path.dirname(p))
        for pattern, role in _PATH_ROLE_PATTERNS:
            if pattern.search(parent + "/"):
                return role
        return "unknown"
    return "unknown"


def _is_business_role(role: str) -> bool:
    return role in _BUSINESS_ROLES


def _is_glue_role(role: str) -> bool:
    return role in _GLUE_ROLES


# ---------------------------------------------------------------------------
# 查询词过滤
# ---------------------------------------------------------------------------

def _filter_generic_words(words: list[str]) -> list[str]:
    """过滤通用格式词和提示词。"""
    out: list[str] = []
    for w in words:
        w = (w or "").strip()
        low = w.lower()
        if not w or len(w) < 2:
            continue
        if low in _GENERIC_OUTPUT_WORDS or low in _GENERIC_PROMPT_WORDS:
            continue
        out.append(w)
    return out


def _is_searchable_content(text: str) -> bool:
    """判断文本是否包含可搜索的内容词（非格式/指令词）。

    对多词短语按空格分割后逐词检查，只要任一子词是可搜索的就返回 True。
    """
    stripped = (text or "").strip()
    if not stripped:
        return False

    low_full = stripped.lower()

    # 整个短语是否直接命中黑名单
    if low_full in _GENERIC_OUTPUT_WORDS or low_full in _GENERIC_PROMPT_WORDS:
        return False

    # CJK 文本 — 如果整体不在黑名单中就是可搜索的
    if _CJK_PATTERN.search(stripped):
        return True

    # CamelCase — 直接可搜索
    if _CAMEL_CASE_PATTERN.search(stripped):
        return True

    if len(stripped) < _CONTENT_WORD_MIN_LEN:
        return False

    # 对每个子词检查（英文短语）
    tokens = stripped.split()
    for token in tokens:
        low = token.lower()
        if not low or len(low) < _CONTENT_WORD_MIN_LEN:
            continue
        if low in _GENERIC_OUTPUT_WORDS or low in _GENERIC_PROMPT_WORDS:
            continue
        if low in _ENGLISH_STOP_WORDS:
            continue
        if _CODE_STRUCTURE_WORD_PATTERN.search(low):
            return True
        if re.fullmatch(r"[a-z]{3,}", low):
            return True

    return False


def _filter_content_terms(checks: list[str]) -> list[str]:
    """从 missed checks 中筛选可搜索的内容词（排除纯格式词和指令词）。"""
    return [c for c in (checks or []) if _is_searchable_content(c)]


def _extract_symbol_hints(text: str) -> list[str]:
    """从文本中提取 CamelCase 符号提示。"""
    if not text:
        return []
    matches = _CAMEL_CASE_PATTERN.findall(text or "")
    java_like = [m for m in matches if m[0].isupper() and len(m) > 3]
    return list(dict.fromkeys(java_like))[:8]


def _extract_content_terms(text: str) -> list[str]:
    """从文本提取可搜索的内容词：CamelCase 符号 + 中文词 + 非指令/非格式的英文词。

    不硬编码任何领域词汇 — 所有不在格式/指令黑名单中的词都是潜在搜索词。
    """
    terms: list[str] = []

    # CamelCase 符号
    terms.extend(_extract_symbol_hints(text))

    # 中文词
    cjk = _CJK_PATTERN.findall(text)
    terms.extend(cjk)

    # 独立英文词（不在 指令/格式/停用词 黑名单中）
    for m in re.findall(r"\b[a-zA-Z][a-zA-Z0-9]{2,}\b", text):
        low = m.lower()
        if low in _GENERIC_PROMPT_WORDS or low in _GENERIC_OUTPUT_WORDS:
            continue
        if low in _ENGLISH_STOP_WORDS:
            continue
        if _CODE_STRUCTURE_WORD_PATTERN.search(low):
            continue  # 代码结构词不作为业务内容词
        terms.append(m)

    seen: set[str] = set()
    out: list[str] = []
    for t in terms:
        lt = t.strip()
        if not lt:
            continue
        low = lt.lower()
        # CJK 词不受 _CONTENT_WORD_MIN_LEN 限制
        if not _CJK_PATTERN.search(lt) and len(low) < _CONTENT_WORD_MIN_LEN:
            continue
        if low not in seen:
            seen.add(low)
            out.append(t)
    return out[:12]


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class CodeQueryPlan:
    """确定性代码查询计划（设计 09 §6.1）。"""

    schema_version: str = "1.0"
    case_id: str = ""
    question: str = ""
    intent_terms: list[str] = field(default_factory=list)
    anchor_refs: list[str] = field(default_factory=list)
    symbol_hints: list[str] = field(default_factory=list)
    trace_targets: list[dict[str, str]] = field(default_factory=list)
    include_roles: list[str] = field(default_factory=list)
    exclude_roles: list[str] = field(default_factory=list)
    missed_checks: list[str] = field(default_factory=list)
    scorer_failure_type: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "case_id": self.case_id,
            "question": self.question,
            "intent_terms": self.intent_terms,
            "anchor_refs": self.anchor_refs,
            "symbol_hints": self.symbol_hints,
            "trace_targets": [
                {"from": t.get("from", ""), "to": t.get("to", "")}
                for t in self.trace_targets
            ],
            "include_roles": self.include_roles,
            "exclude_roles": self.exclude_roles,
            "missed_checks": self.missed_checks,
            "scorer_failure_type": self.scorer_failure_type,
        }


@dataclass
class CodeCandidate:
    """单条代码候选结果（设计 09 §6.2）。"""

    ref: str = ""
    path: str = ""
    symbol: str = ""
    kind: str = ""
    role: str = ""
    source: str = ""
    score: float = 0.0
    score_reasons: list[str] = field(default_factory=list)
    snippet: str = ""
    call_chain: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "path": self.path,
            "symbol": self.symbol,
            "kind": self.kind,
            "role": self.role,
            "source": self.source,
            "score": self.score,
            "score_reasons": self.score_reasons,
            "snippet_preview": (self.snippet or "")[:200],
            "call_chain": self.call_chain,
        }


@dataclass
class CodeFact:
    """提取的代码事实（设计 09 §6.3）。"""

    fact_id: str = ""
    case_id: str = ""
    statement: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    evidence_quotes: list[str] = field(default_factory=list)
    confidence: float = 0.0
    source: str = ""
    role: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "case_id": self.case_id,
            "statement": self.statement,
            "evidence_refs": self.evidence_refs,
            "evidence_quotes": self.evidence_quotes,
            "confidence": self.confidence,
            "source": self.source,
            "role": self.role,
        }


@dataclass
class CodeRetrievalResult:
    """find_relevant_code 的聚合返回。"""

    candidates: list[CodeCandidate] = field(default_factory=list)
    facts: list[CodeFact] = field(default_factory=list)
    query_plan: CodeQueryPlan | None = None
    metrics: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Query Plan 构建
# ---------------------------------------------------------------------------

def build_code_query_plan(
    item_or_result: dict,
    *,
    graph_sidecars: Any = None,
    scorer_diagnostics: dict | None = None,
    atom_ids: list[str] | None = None,
    source_atom_ids: list[str] | None = None,
) -> CodeQueryPlan:
    """从失败 item/result 构建 CodeQueryPlan（设计 09 §7.1）。

    规则：
    1. context_refs 是第一优先级 anchor
    2. source_atom_ids 可反查 evidence_index
    3. missed checks 中只保留可搜索业务词
    4. question 中提取 CamelCase、英文业务词
    5. scorer diagnostics 若提供 required_concepts / failure_type
    """
    item = item_or_result or {}
    case_id = str(item.get("id") or "")
    question = str(item.get("question") or "")[:200]
    missed_checks = [str(c).strip() for c in (item.get("missed_checks") or []) if c]

    anchor_refs = [str(r).strip() for r in (item.get("context_refs") or []) if r][:4]

    symbol_hints: list[str] = []
    if graph_sidecars is not None:
        store = getattr(graph_sidecars, "evidence_index", None)
        if store is not None:
            for atom_id in (source_atom_ids or atom_ids or []):
                hits = store.lookup_atom(atom_id)
                for hit in hits[:1]:
                    if hit.file_path:
                        sym = os.path.splitext(os.path.basename(hit.file_path))[0]
                        if sym:
                            symbol_hints.append(sym)

    business_checks = _filter_content_terms(missed_checks)

    term_hints = _extract_symbol_hints(question)
    biz_terms = _extract_content_terms(question)

    for ref in anchor_refs:
        if "#" in ref:
            sym = ref.rsplit("#", 1)[1]
            if sym and sym not in symbol_hints:
                symbol_hints.append(sym)
        elif "::" in ref:
            sym = ref.rsplit("::", 1)[1]
            if sym and sym not in symbol_hints:
                symbol_hints.append(sym)

    all_symbols: list[str] = []
    seen_sym: set[str] = set()
    for s in symbol_hints + term_hints:
        s = s.strip()
        if s and s not in seen_sym:
            seen_sym.add(s)
            all_symbols.append(s)

    trace_targets: list[dict[str, str]] = []
    if graph_sidecars is not None:
        entrypoints = getattr(graph_sidecars, "entrypoints", None)
        for ref in anchor_refs[:2]:
            path_part, symbol_part = ("", "")
            if "#" in ref:
                path_part, symbol_part = ref.rsplit("#", 1)
            elif "::" in ref:
                path_part, symbol_part = ref.rsplit("::", 1)
            if not symbol_part:
                continue
            stem = os.path.splitext(os.path.basename(path_part))[0]
            if stem and stem != symbol_part:
                from_entry = ""
                if entrypoints is not None:
                    ep_id = str(item.get("entrypoint_id") or "")
                    if ep_id:
                        ep = entrypoints.lookup(ep_id)
                        if ep:
                            from_entry = ep.kind or ""
                    if not from_entry:
                        from_entry = entrypoints.resolve_from_entry(
                            file_path=path_part, symbol=symbol_part,
                        )
                trace_targets.append({
                    "from": stem,
                    "to": symbol_part,
                    "from_entry": from_entry,
                })

    scorer_diag = scorer_diagnostics or item.get("scorer_diagnostics") or {}
    failure_type = str(scorer_diag.get("failure_type") or item.get("diagnosis_failure_type") or "")

    include_roles = list(_BUSINESS_ROLES)
    exclude_roles = list(_GLUE_ROLES)

    intent_terms = business_checks + [t for t in biz_terms if t not in business_checks]
    # 排除 scorer 诊断词（会计凭证/借/贷/借贷校验等），它们对代码搜索价值低
    intent_terms = [
        t for t in intent_terms
        if t.lower() not in _SCORER_DIAGNOSTIC_TERMS
    ]

    return CodeQueryPlan(
        case_id=case_id,
        question=question,
        intent_terms=intent_terms[:12],
        anchor_refs=anchor_refs,
        symbol_hints=all_symbols[:8],
        trace_targets=trace_targets[:3],
        include_roles=include_roles,
        exclude_roles=exclude_roles,
        missed_checks=missed_checks[:8],
        scorer_failure_type=failure_type or "unknown",
    )


# ---------------------------------------------------------------------------
# 多路召回
# ---------------------------------------------------------------------------

def _execute_code_tool(
    code_tools: Any,
    tool_name: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    if code_tools is None or not hasattr(code_tools, "execute"):
        return {}
    try:
        raw = code_tools.execute({
            "function": {
                "name": tool_name,
                "arguments": json.dumps(args),
            },
        })
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.debug("code tool %s error: %s", tool_name, exc)
        return {}


def _search_anchor_refs(
    plan: CodeQueryPlan,
    code_tools: Any,
    candidates: list[CodeCandidate],
) -> None:
    """通道 1: 读取 benchmark 明确引用的代码。"""
    for ref in plan.anchor_refs:
        file_path, symbol = ("", "")
        if "#" in ref:
            file_path, symbol = ref.rsplit("#", 1)
        elif "::" in ref:
            file_path, symbol = ref.rsplit("::", 1)
        else:
            file_path = ref

        role = _classify_code_role(file_path, symbol)
        if role in plan.exclude_roles:
            continue

        if symbol:
            data = _execute_code_tool(code_tools, "explore_symbol", {
                "symbol": symbol,
                "include_source": True,
                "max_lines": 60,
            })
            if data and not data.get("error"):
                candidates.append(CodeCandidate(
                    ref=ref,
                    path=data.get("file_path", file_path),
                    symbol=symbol,
                    kind=data.get("kind", ""),
                    role=role,
                    source="context_ref",
                    score=0.8 if _is_business_role(role) else 0.4,
                    score_reasons=["context_ref_hit"] + (
                        ["business_logic_role"] if _is_business_role(role) else []
                    ),
                    snippet=(data.get("source") or "")[:1200],
                ))
                continue

        if file_path:
            data = _execute_code_tool(code_tools, "read_code_file", {
                "path": file_path,
                "end_line": 80,
            })
            if data.get("content"):
                candidates.append(CodeCandidate(
                    ref=ref,
                    path=file_path,
                    symbol=symbol,
                    kind="file",
                    role=role,
                    source="context_ref",
                    score=0.3,
                    score_reasons=["context_ref_hit", "file_only"],
                    snippet=str(data["content"])[:1200],
                ))


def _search_symbols(
    plan: CodeQueryPlan,
    code_tools: Any,
    candidates: list[CodeCandidate],
) -> None:
    """通道 2: 符号搜索。"""
    searched: set[str] = set()
    for hint in plan.symbol_hints[:5]:
        if hint in searched:
            continue
        searched.add(hint)
        data = _execute_code_tool(code_tools, "search_symbol", {
            "query": hint,
            "max_results": 5,
        })
        for result in (data.get("results") or [])[:3]:
            name = result.get("name", hint)
            file_path = result.get("file_path", "")
            role = _classify_code_role(file_path, name, result.get("kind", ""))
            if role in plan.exclude_roles:
                continue
            candidates.append(CodeCandidate(
                ref=f"{file_path}#{name}",
                path=file_path,
                symbol=name,
                kind=result.get("kind", ""),
                role=role,
                source="symbol_search",
                score=0.45 + (0.2 if _is_business_role(role) else 0.0),
                score_reasons=["symbol_search"] + (
                    ["business_logic_role"] if _is_business_role(role) else []
                ),
            ))


def _search_traces(
    plan: CodeQueryPlan,
    code_tools: Any,
    candidates: list[CodeCandidate],
) -> None:
    """通道 3: trace 符号调用链。"""
    for target in plan.trace_targets[:2]:
        from_sym = target.get("from", "")
        to_sym = target.get("to", "")
        from_entry = target.get("from_entry", "")
        if not from_sym or not to_sym:
            continue
        args: dict[str, Any] = {
            "symbol": from_sym,
            "to_symbol": to_sym,
            "direction": "callees",
            "depth": 2,
            "path_max_depth": 10,
        }
        if from_entry:
            args["from_entry"] = from_entry
        data = _execute_code_tool(code_tools, "trace_symbol", args)
        paths = data.get("paths_to") or []
        if paths:
            summary = paths[0].get("summary", "")
            candidates.append(CodeCandidate(
                ref=f"{from_sym}->{to_sym}",
                path="",
                symbol=to_sym,
                kind="trace",
                role="call_chain",
                source="trace",
                score=0.5,
                score_reasons=["trace_hit", "call_chain_exists"],
                call_chain=summary,
            ))
            break
        callees = data.get("callees") or []
        for callee in callees[:2]:
            name = callee.get("name", "")
            if name and name != to_sym:
                candidates.append(CodeCandidate(
                    ref=f"{from_sym}->{name}",
                    path=callee.get("file_path", ""),
                    symbol=name,
                    kind=callee.get("kind", ""),
                    role="unknown",
                    source="trace",
                    score=0.3,
                    score_reasons=["trace_nearby_callee"],
                ))


def _search_content(
    plan: CodeQueryPlan,
    code_tools: Any,
    candidates: list[CodeCandidate],
) -> None:
    """通道 4: 内容搜索（search_code + get_code_context）。"""
    search_terms = plan.intent_terms[:4]
    if not search_terms:
        search_terms = plan.symbol_hints[:2]
    if not search_terms and plan.missed_checks:
        business = _filter_content_terms(plan.missed_checks)
        search_terms = business[:2]

    for term in search_terms[:3]:
        data = _execute_code_tool(code_tools, "search_code", {
            "query": term,
            "max_results": 5,
        })
        for result in (data.get("results") or [])[:3]:
            file_path = result.get("file_path") or result.get("path", "")
            role = _classify_code_role(file_path, "")
            if role in plan.exclude_roles:
                continue
            scores = ["content_search"]
            if _is_business_role(role):
                scores.append("business_logic_role")
            candidates.append(CodeCandidate(
                ref=file_path,
                path=file_path,
                symbol="",
                kind="file",
                role=role,
                source="content_search",
                score=0.3 + (0.15 if _is_business_role(role) else 0.0),
                score_reasons=scores,
                snippet=(result.get("content") or result.get("snippet", ""))[:800],
            ))

        ctx_data = _execute_code_tool(code_tools, "get_code_context", {
            "query": term,
            "max_blocks": 3,
        })
        for blk in (ctx_data.get("blocks") or [])[:2]:
            file_path = blk.get("file_path", "")
            name = blk.get("symbol", "")
            role = _classify_code_role(file_path, name, blk.get("kind", ""))
            if role in plan.exclude_roles:
                continue
            scores = ["graph_context"]
            if _is_business_role(role):
                scores.append("business_logic_role")
            candidates.append(CodeCandidate(
                ref=f"{file_path}#{name}" if name else file_path,
                path=file_path,
                symbol=name,
                kind=blk.get("kind", ""),
                role=role,
                source="content_search",
                score=0.35 + (0.2 if _is_business_role(role) else 0.0),
                score_reasons=scores,
                snippet=(blk.get("content") or "")[:800],
            ))


def _search_evidence_index(
    plan: CodeQueryPlan,
    graph_sidecars: Any,
    candidates: list[CodeCandidate],
) -> None:
    """通道 5: evidence_index 侧车查询。"""
    if graph_sidecars is None:
        return
    store = getattr(graph_sidecars, "evidence_index", None)
    if store is None:
        return
    for ref in plan.anchor_refs:
        hits = store.lookup_ref(ref)
        for hit in hits[:2]:
            role = _classify_code_role(hit.file_path or "", "")
            if getattr(hit, "relevance", None) is not None and float(hit.relevance) < 0.3:
                continue
            candidates.append(CodeCandidate(
                ref=hit.evidence_id,
                path=hit.file_path or "",
                symbol=getattr(hit, "symbol", "") or "",
                kind=getattr(hit, "kind", "") or "",
                role=role,
                source="evidence_index",
                score=0.7 if _is_business_role(role) else 0.35,
                score_reasons=["evidence_index_hit"] + (
                    ["business_logic_role"] if _is_business_role(role) else []
                ),
                snippet=(getattr(hit, "evidence", "") or getattr(hit, "snippet", ""))[:800],
            ))


# ---------------------------------------------------------------------------
# 角色感知 Rerank（Phase 2）
# ---------------------------------------------------------------------------

def _role_aware_rerank(
    candidates: list[CodeCandidate],
    plan: CodeQueryPlan,
) -> list[CodeCandidate]:
    """角色感知 rerank（设计 09 §7.3）。"""
    anchor_names: set[str] = set()
    for ref in plan.anchor_refs:
        if "#" in ref:
            anchor_names.add(ref.rsplit("#", 1)[1])
        elif "::" in ref:
            anchor_names.add(ref.rsplit("::", 1)[1])

    for cand in candidates:
        reasons = list(cand.score_reasons or [])

        anchor_score = 0.0
        if "context_ref_hit" in reasons:
            anchor_score = 1.0 if (cand.symbol and cand.symbol in anchor_names) else 0.7

        role_score = 0.0
        if _is_business_role(cand.role):
            role_score = 1.0
        elif cand.role == "call_chain":
            role_score = 0.6
        elif cand.role == "unknown":
            role_score = 0.3

        semantic_match = 0.0
        if cand.source in ("symbol_search", "content_search"):
            sym_low = (cand.symbol or "").lower()
            for term in plan.intent_terms:
                if term.lower() in sym_low or sym_low in term.lower():
                    semantic_match = 0.8
                    break
            if semantic_match == 0.0:
                snippet_low = (cand.snippet or "").lower()
                for term in plan.intent_terms[:3]:
                    if term.lower() in snippet_low:
                        semantic_match = 0.5
                        break
            if semantic_match == 0.0 and cand.source == "symbol_search":
                semantic_match = 0.3
        elif cand.source == "context_ref":
            semantic_match = 0.8
        elif cand.source in ("trace",):
            semantic_match = 0.6

        call_chain_score = 0.0
        if cand.call_chain:
            call_chain_score = 1.0
        elif "call_chain_exists" in reasons or "trace_hit" in reasons:
            call_chain_score = 0.8

        evidence_index_score = 1.0 if cand.source == "evidence_index" else 0.0

        glue_penalty = 0.0
        if _is_glue_role(cand.role) or cand.role in plan.exclude_roles:
            glue_penalty = 1.0
        elif cand.role == "handler_only":
            glue_penalty = 0.7

        cand.score = (
            0.35 * anchor_score
            + 0.25 * role_score
            + 0.20 * semantic_match
            + 0.10 * call_chain_score
            + 0.10 * evidence_index_score
            - 0.25 * glue_penalty
        )

        new_reasons = []
        if anchor_score > 0:
            new_reasons.append(f"anchor={anchor_score:.2f}")
        if role_score > 0:
            new_reasons.append(f"role={role_score:.2f}")
        if semantic_match > 0:
            new_reasons.append(f"semantic={semantic_match:.2f}")
        if call_chain_score > 0:
            new_reasons.append(f"callchain={call_chain_score:.2f}")
        if evidence_index_score > 0:
            new_reasons.append(f"evidence_idx={evidence_index_score:.2f}")
        if glue_penalty > 0:
            new_reasons.append(f"glue_penalty=-{glue_penalty:.2f}")
        cand.score_reasons = new_reasons

    seen_refs: set[str] = set()
    unique: list[CodeCandidate] = []
    for cand in sorted(candidates, key=lambda c: -c.score):
        key = f"{cand.path}#{cand.symbol}" if cand.symbol else cand.path
        if key in seen_refs:
            continue
        seen_refs.add(key)
        unique.append(cand)
    return unique


# ---------------------------------------------------------------------------
# CodeFact 提取
# ---------------------------------------------------------------------------

def _extract_code_facts_from_candidates(
    candidates: list[CodeCandidate],
    plan: CodeQueryPlan,
) -> list[CodeFact]:
    """从高置信度候选结果提取业务事实摘要（设计 09 升级版）。

    结构化提取：方法调用、枚举、借贷/账户/金额变量。
    """
    facts: list[CodeFact] = []
    for cand in candidates[:8]:
        if cand.score < 0.3:
            continue
        if cand.role in plan.exclude_roles:
            continue
        if not _is_business_role(cand.role) and cand.role != "call_chain" and cand.score < 0.6:
            continue

        snippet = cand.snippet or ""
        effective_lines = _get_effective_lines(snippet)

        # 结构化提取
        statement = _build_business_statement(cand, effective_lines)
        if not statement:
            continue

        # 提取证据引文（优先包含业务变量的代码行）
        quotes = _extract_business_quotes(effective_lines)

        confidence_sources = {
            "context_ref": 0.85,
            "evidence_index": 0.8,
            "trace": 0.75,
            "symbol_search": 0.55,
            "content_search": 0.4,
        }
        base_conf = confidence_sources.get(cand.source, 0.4)
        role_bonus = 0.1 if _is_business_role(cand.role) else 0.0
        score_bonus = min(0.1, cand.score * 0.15)
        confidence = min(0.95, base_conf + role_bonus + score_bonus)

        fact_id = _make_fact_id(plan.case_id, cand.symbol or cand.path)

        facts.append(CodeFact(
            fact_id=fact_id,
            case_id=plan.case_id,
            statement=statement[:300],
            evidence_refs=[
                f"{cand.path}#{cand.symbol}" if cand.symbol else cand.path
            ],
            evidence_quotes=quotes[:3],
            confidence=round(confidence, 2),
            source=cand.source,
            role=cand.role,
        ))
    return facts


# ── 业务事实提取 helpers ──────────────────────────────────────────────

# 金融业务变量关键词
_CREDIT_KEYWORDS = {"credit", "credited", "cred"}
_DEBIT_KEYWORDS = {"debit", "debited", "deb"}
_AMOUNT_KEYWORDS = {"amount", "transactionamount", "tranamount", "money", "value", "price"}
_ACCOUNT_KEYWORDS = {"account", "accountid", "glaccount", "glaccountid", "officeid", "savings", "loan"}

# Java 方法调用模式：identifier.identifier.methodName(...)
_METHOD_CALL_RE = re.compile(
    r'(\w[\w.]*\.)?(\w[\w]*)\.(\w[\w]*)\((.*?)\)',
    re.IGNORECASE,
)

# Java 枚举值模式：ClassName.VALUE 或 EnumName.XXX
_ENUM_REF_RE = re.compile(
    r'\b([A-Z][a-zA-Z0-9_]+Enum(?:erations)?|' +
    r'[A-Z][a-zA-Z0-9_]+Type|' +
    r'[A-Z][a-zA-Z0-9_]+Enums|' +
    r'[A-Z][a-zA-Z0-9_]+Status)\s*\.\s*([A-Z_][A-Z0-9_]*)',
)

# 嵌入式枚举/常量引用
_ENUM_CONST_RE = re.compile(
    r'\b([A-Z][a-zA-Z0-9_]{3,})\.([A-Z_][A-Z0-9_]{2,})\b',
)


def _get_effective_lines(snippet: str) -> list[str]:
    """提取有效代码行（非注释、非 import/package）。"""
    return [
        l.strip()[:200]
        for l in snippet.split("\n")
        if l.strip()
        and not l.strip().startswith(("//", "*", "/*", "import", "package"))
        and not l.strip().startswith(("@", "package "))
    ]


def _build_business_statement(cand: CodeCandidate, lines: list[str]) -> str:
    """从代码候选构建业务事实摘要语句。

    策略：
    1. 提取借贷分录相关调用（createDebitJournalEntry* / createCreditJournalEntry* /
       createJournalEntriesForLoan），从中提取账户类型和金额
    2. 提取其他有意义的方法调用关系
    3. 提取枚举/常量引用
    4. 提取金融变量（借贷/账户/金额）
    5. 回退到符号名 + 首行摘要
    """
    all_text = " ".join(lines)

    # 1. 提取借贷分录调用（增强版：跟踪参数）
    debit_credit_facts = _extract_debit_credit_call_facts(all_text)

    # 2. 提取方法调用
    method_calls = _extract_method_calls(all_text)
    # 3. 提取枚举/常量引用
    enum_refs = _extract_enum_refs(all_text)
    # 4. 提取金融变量
    fin_vars = _extract_financial_variables(lines)

    statement_parts: list[str] = []

    # 符号描述
    if cand.symbol:
        prefix = ""
        if cand.kind in ("method", "function"):
            prefix = f"Method {cand.symbol}"
        elif cand.kind in ("class", "interface"):
            prefix = f"Class {cand.symbol}"
        else:
            prefix = f"{cand.symbol}"

        if cand.role:
            prefix += f" ({cand.role})"

        # 借贷分录事实（优先级最高）
        if debit_credit_facts:
            statement_parts.append(f"{prefix}: {debit_credit_facts}")
        # 方法调用事实
        elif method_calls:
            unique_calls = list(dict.fromkeys(method_calls))[:3]
            calls_str = ", ".join(unique_calls)
            statement_parts.append(f"{prefix} calls: {calls_str}")
        # 枚举引用事实
        elif enum_refs:
            unique_enums = list(dict.fromkeys(enum_refs))[:3]
            enums_str = ", ".join(unique_enums)
            statement_parts.append(f"{prefix} references enum(s): {enums_str}")
        # 金融变量事实
        elif fin_vars:
            statement_parts.append(f"{prefix} uses: {', '.join(fin_vars)}")
        else:
            if lines:
                statement_parts.append(f"{prefix} - {lines[0][:120]}")
            else:
                statement_parts.append(prefix)

    # 符号缺失时用首行
    elif lines:
        snippet_preview = lines[0][:120]
        if debit_credit_facts:
            statement_parts.append(debit_credit_facts)
        elif method_calls:
            statement_parts.append(f"calls: {', '.join(list(dict.fromkeys(method_calls))[:3])}")
        elif enum_refs:
            statement_parts.append(f"refs enum(s): {', '.join(list(dict.fromkeys(enum_refs))[:3])}")
        elif fin_vars:
            statement_parts.append(f"uses financial vars: {', '.join(fin_vars)}")
        else:
            statement_parts.append(snippet_preview)

    return " | ".join(statement_parts)


# 借贷分录方法名模式
_DEBIT_CREDIT_CALL_PATTERN = re.compile(
    r'\b(?:helper|this\.helper)?\.(createDebitJournalEntryForLoan'
    r'|createCreditJournalEntryForLoan'
    r'|createJournalEntriesForLoan'
    r'|createDebitJournalEntryForLoanCharges'
    r'|createCreditJournalEntryForLoanCharges'
    r')\((.*?)\)',
    re.IGNORECASE,
)

# 会计科目类型名映射（AccountingConstants 中的枚举名 → 中文含义）
_ACCOUNT_TYPE_MEANING: dict[str, str] = {
    "FUND_SOURCE": "资金来源",
    "LOAN_PORTFOLIO": "贷款组合(资产)",
    "INTEREST_ON_LOANS": "贷款利息收入",
    "INCOME_FROM_FEES": "费用收入",
    "INCOME_FROM_PENALTIES": "罚金收入",
    "LOSSES_WRITTEN_OFF": "核销损失",
    "INTEREST_RECEIVABLE": "应收利息",
    "FEES_RECEIVABLE": "应收费用",
    "PENALTIES_RECEIVABLE": "应收罚金",
    "TRANSFERS_SUSPENSE": "转账暂记",
    "OVERPAYMENT": "超额还款(负债)",
    "GOODWILL_CREDIT": "商誉贷记",
    "INCOME_FROM_RECOVERY": "回收收入",
    "INCOME_FROM_CHARGE_OFF_INTEREST": "核销利息回收",
    "INCOME_FROM_CHARGE_OFF_FEES": "核销费用回收",
    "CHARGE_OFF_EXPENSE": "核销费用",
    "INCOME_FROM_CHARGE_OFF_PENALTY": "核销罚金回收",
    "DEFERRED_INCOME_LIABILITY": "递延收入债务",
}


def _extract_debit_credit_call_facts(text: str) -> str:
    """从代码文本提取借贷分录调用的业务事实。

    识别 createDebitJournalEntry* / createCreditJournalEntry* /
    createJournalEntriesForLoan 等调用，提取账户类型参数。
    返回摘要字符串，如 "debits FUND_SOURCE(资金来源), credits LOAN_PORTFOLIO(贷款组合资产)"。
    """
    facts: list[str] = []
    seen: set[str] = set()

    for m in _DEBIT_CREDIT_CALL_PATTERN.finditer(text):
        method_name = m.group(1)
        args_str = m.group(2)

        # 提取账户类型参数（AccountingConstants 枚举名引用）
        account_types = re.findall(
            r'\b(?:Cash|Accrual)?AccountsFor(?:Loan|Savings)\.(\w+)\b',
            args_str,
        )
        # 也提取 AccountingConstants.AccrualAccountsForLoan.XXX 格式
        account_types += re.findall(
            r'AccrualAccountsForLoan\.(\w+)\b',
            args_str,
        )
        account_types += re.findall(
            r'CashAccountsForLoan\.(\w+)\b',
            args_str,
        )

        # 提取金额参数（通过变量名或字面量推断）
        amount_hints = []
        amount_var_match = re.search(
            r'\b(\w*amount\w*|transactionPartAmount|grossAmount|netAmount)\b',
            args_str, re.IGNORECASE,
        )
        if amount_var_match:
            amount_hints.append(f"amount={amount_var_match.group(1)}")

        # 构建事实
        fact_parts = []
        if "debit" in method_name.lower():
            prefix = "debit"
        elif "credit" in method_name.lower():
            prefix = "credit"
        else:
            prefix = "journal"

        if account_types:
            for at in account_types[:2]:
                meaning = _ACCOUNT_TYPE_MEANING.get(at, at)
                key = f"{prefix}_{at}"
                if key not in seen:
                    seen.add(key)
                    fact_str = f"{prefix}→{at}"
                    if meaning and meaning != at:
                        fact_str += f"({meaning})"
                    facts.append(fact_str)

        if amount_hints and not account_types:
            facts.append(f"{prefix} {', '.join(amount_hints)}")

    if not facts:
        return ""

    return ", ".join(facts[:4])


def _extract_method_calls(text: str) -> list[str]:
    """从代码文本提取有意义的业务方法调用。

    返回去噪后的调用列表（过滤 setter/getter/toString 等）。
    """
    calls: list[str] = []
    seen: set[str] = set()
    for m in _METHOD_CALL_RE.finditer(text):
        call_name = m.group(3)
        # 过滤 trivial 方法
        if call_name in ("get", "set", "toString", "equals", "hashCode", "add", "put", "remove", "size", "isEmpty"):
            continue
        if call_name.startswith(("get", "set", "is")) and len(call_name) > 3 and call_name[3].isupper():
            continue  # getXxx / setXxx
        call_str = f"{call_name}()"
        if call_str not in seen:
            seen.add(call_str)
            calls.append(call_str)
    return calls


def _extract_enum_refs(text: str) -> list[str]:
    """从代码文本提取枚举/常量引用。"""
    refs: list[str] = []
    seen: set[str] = set()
    for m in _ENUM_REF_RE.finditer(text):
        enum_name = m.group(1)
        value = m.group(2)
        ref_str = f"{enum_name}.{value}"
        if ref_str not in seen:
            seen.add(ref_str)
            refs.append(ref_str)
    # 回退到通用常量引用
    if not refs:
        for m in _ENUM_CONST_RE.finditer(text):
            prefix = m.group(1)
            if prefix in ("import", "package", "class", "public", "private", "protected", "static", "final", "return", "throw", "new"):
                continue
            val = m.group(2)
            ref_str = f"{prefix}.{val}"
            if ref_str not in seen:
                seen.add(ref_str)
                refs.append(ref_str)
    return refs


def _extract_financial_variables(lines: list[str]) -> list[str]:
    """从代码行提取金融业务变量（借贷/账户/金额/枚举）。"""
    vars_found: dict[str, str] = {}  # 变量名 → 类别

    for line in lines:
        # 扫描标识符
        words = re.findall(r'\b([a-zA-Z_]\w{2,})\b', line)
        word_lower_set = {w.lower() for w in words}

        for w in words:
            wl = w.lower()
            if wl in _CREDIT_KEYWORDS:
                vars_found[w] = "credit"
            elif wl in _DEBIT_KEYWORDS:
                vars_found[w] = "debit"
            elif wl in _AMOUNT_KEYWORDS:
                vars_found[w] = "amount"
            elif wl in _ACCOUNT_KEYWORDS:
                vars_found[w] = "account"

        # 也检查组合词（如 debitAccountId → matches debit+account）
        for w in words:
            wl = w.lower()
            if wl not in vars_found:
                if any(k in wl for k in _CREDIT_KEYWORDS) and any(k in wl for k in _ACCOUNT_KEYWORDS):
                    vars_found[w] = "credit_account"
                elif any(k in wl for k in _DEBIT_KEYWORDS) and any(k in wl for k in _ACCOUNT_KEYWORDS):
                    vars_found[w] = "debit_account"
                elif any(k in wl for k in _CREDIT_KEYWORDS) and any(k in wl for k in _AMOUNT_KEYWORDS):
                    vars_found[w] = "credit_amount"
                elif any(k in wl for k in _DEBIT_KEYWORDS) and any(k in wl for k in _AMOUNT_KEYWORDS):
                    vars_found[w] = "debit_amount"

    # 按类别排序输出
    priority = {"debit_account": 0, "credit_account": 1, "debit_amount": 2, "credit_amount": 3,
                "debit": 4, "credit": 5, "account": 6, "amount": 7}
    sorted_vars = sorted(vars_found.items(), key=lambda x: priority.get(x[1], 99))
    return [f"{var} ({cat})" for var, cat in sorted_vars[:6]]


def _extract_business_quotes(lines: list[str]) -> list[str]:
    """提取优先包含业务变量/方法调用的证据行。

    优先选择含业务关键词的行，不足 3 行时补首行。
    """
    business_lines: list[str] = []
    other_lines: list[str] = []

    biz_kws = _CREDIT_KEYWORDS | _DEBIT_KEYWORDS | _AMOUNT_KEYWORDS | _ACCOUNT_KEYWORDS
    biz_kws.update({"enum", "create", "process", "validate", "accounting", "journal", "entry", "transaction"})

    for line in lines:
        if any(kw in line.lower() for kw in biz_kws):
            business_lines.append(line)
        else:
            other_lines.append(line)

    return (business_lines + other_lines)[:3]


def _make_fact_id(case_id: str, name: str) -> str:
    name_slug = re.sub(r"[^a-zA-Z0-9_]+", "_", (name or "unknown"))[:40].strip("_")
    prefix = re.sub(r"[^a-zA-Z0-9_]+", "_", case_id or "unknown")[:30].strip("_")
    digest = sha1(f"{case_id}_{name}".encode()).hexdigest()[:8]
    return f"fact_{prefix}_{name_slug}_{digest}"


# ---------------------------------------------------------------------------
# 主入口：find_relevant_code
# ---------------------------------------------------------------------------

def find_relevant_code(
    item_or_result: dict,
    code_tools: Any,
    *,
    graph_sidecars: Any = None,
    scorer_diagnostics: dict | None = None,
    max_candidates: int = 8,
    max_snippet_chars: int = 1200,
    atom_ids: list[str] | None = None,
    source_atom_ids: list[str] | None = None,
) -> CodeRetrievalResult:
    """聚合检索入口（设计 09 §7.2）。

    1. 生成 QueryPlan
    2. 执行多路并行召回
    3. 角色感知 rerank
    4. 提取 CodeFact
    """
    plan = build_code_query_plan(
        item_or_result,
        graph_sidecars=graph_sidecars,
        scorer_diagnostics=scorer_diagnostics,
        atom_ids=atom_ids,
        source_atom_ids=source_atom_ids,
    )

    candidates: list[CodeCandidate] = []

    _search_anchor_refs(plan, code_tools, candidates)
    _search_symbols(plan, code_tools, candidates)
    _search_traces(plan, code_tools, candidates)
    _search_content(plan, code_tools, candidates)
    _search_evidence_index(plan, graph_sidecars, candidates)

    ranked = _role_aware_rerank(candidates, plan)
    top = ranked[:max_candidates]

    for c in top:
        if len(c.snippet or "") > max_snippet_chars:
            c.snippet = c.snippet[:max_snippet_chars]

    facts = _extract_code_facts_from_candidates(top, plan)

    metrics = {
        "query_plan": plan.to_dict(),
        "total_candidates": len(candidates),
        "ranked_candidates": len(ranked),
        "top_candidates": len(top),
        "facts_extracted": len(facts),
        "cases_with_facts": 1 if facts else 0,
        "glue_hits": sum(1 for c in top if _is_glue_role(c.role) or c.role == "handler_only"),
        "business_hits": sum(1 for c in top if _is_business_role(c.role)),
        "top_role": top[0].role if top else "none",
        "top_score": top[0].score if top else 0.0,
        "top_source": top[0].source if top else "none",
    }

    return CodeRetrievalResult(
        candidates=top,
        facts=facts,
        query_plan=plan,
        metrics=metrics,
    )


# ---------------------------------------------------------------------------
# 辅助：将 CodeFact 格式化为 rollout/reflect 上下文
# ---------------------------------------------------------------------------

def format_code_facts_for_context(facts: list[CodeFact], max_facts: int = 5) -> str:
    """将 CodeFact 列表格式化为文本上下文。"""
    if not facts:
        return ""
    lines = ["--- Project code facts ---"]
    for fact in facts[:max_facts]:
        lines.append(f"- [fact] {fact.statement}")
        if fact.evidence_refs:
            lines.append(f"  Evidence: {', '.join(fact.evidence_refs[:2])}")
        if fact.evidence_quotes:
            for quote in fact.evidence_quotes[:2]:
                lines.append(f"  > {quote[:200]}")
    return "\n".join(lines)


def format_candidates_for_context(
    candidates: list[CodeCandidate], max_candidates: int = 4,
) -> str:
    """将 CodeCandidate 列表格式化为文本上下文。"""
    if not candidates:
        return ""
    lines = ["--- Code candidates (ordered by relevance) ---"]
    for cand in candidates[:max_candidates]:
        parts = [f"  [{cand.role}] {cand.symbol or cand.path}"]
        if cand.score:
            parts.append(f"(score={cand.score:.2f})")
        lines.append(" ".join(parts))
        if cand.call_chain:
            lines.append(f"    chain: {cand.call_chain}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 批量检索
# ---------------------------------------------------------------------------

def batch_find_relevant_code(
    items: list[dict],
    code_tools: Any,
    *,
    graph_sidecars: Any = None,
    scorer_diagnostics: dict | None = None,
    max_candidates: int = 8,
) -> dict[str, CodeRetrievalResult]:
    """对多个 item 批量执行代码检索。"""
    results: dict[str, CodeRetrievalResult] = {}
    for item in items:
        case_id = str(item.get("id") or "")
        if not case_id:
            continue
        results[case_id] = find_relevant_code(
            item,
            code_tools,
            graph_sidecars=graph_sidecars,
            scorer_diagnostics=scorer_diagnostics,
            max_candidates=max_candidates,
        )
    return results
