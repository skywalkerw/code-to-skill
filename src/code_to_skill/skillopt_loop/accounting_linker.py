"""Benchmark 失败 case → 图谱会计实现类查询映射。"""
from __future__ import annotations

import re

# benchmark id 前缀 / 问题关键词 → 图谱搜索词
_CASE_GRAPH_QUERIES: dict[str, list[str]] = {
    "jv_purchase": ["JournalEntry inventory purchase", "AccountingProcessor"],
    "jv_loan_disburse": ["CashBasedAccountingProcessorForLoan disburse", "createJournalEntriesForDisbursements"],
    "jv_repayment": ["AccountingProcessorForLoan repayment", "createJournalEntriesForRepayments"],
    "jv_fee": ["Charge fee accounting", "AccountingProcessorForLoan"],
    "jv_accrual": ["AccrualBasedAccountingProcessorForLoan accrual", "createJournalEntriesForAccruals"],
    "jv_sale": ["JournalEntry sale income", "AccountingProcessor"],
    "jv_savings": ["Savings accounting deposit", "AccountingProcessorForSavings"],
    "jv_writeoff": ["loan writeoff accounting", "AccountingProcessor"],
}

# benchmark case → (起点符号, 终点符号) 用于 trace_symbol 调用链证据
_CASE_TRACE_PAIRS: dict[str, list[tuple[str, str]]] = {
    "jv_loan_disburse": [
        ("AccountingProcessorForLoan", "createJournalEntriesForDisbursements"),
    ],
    "jv_repayment": [
        ("AccountingProcessorForLoan", "createJournalEntriesForRepayments"),
    ],
    "jv_accrual": [
        ("AccrualBasedAccountingProcessorForLoan", "createJournalEntriesForAccruals"),
    ],
    "jv_purchase": [
        ("JournalEntriesApiResource", "createJournalEntriesForPurchase"),
        ("AccountingProcessorForShares", "createJournalEntriesForPurchase"),
    ],
    "jv_fee": [
        ("AccountingProcessorForLoan", "createJournalEntriesForLoanCharges"),
    ],
}

_CHECK_GRAPH_QUERIES: dict[str, str] = {
    "库存": "inventory stock accounting",
    "银行": "bank payment accounting",
    "现金": "cash accounting",
    "贷款": "loan accounting processor",
    "发放": "disburse loan journal",
    "还款": "repayment principal interest",
    "计提": "accrual interest receivable",
    "应收利息": "interest receivable accrual",
    "费用": "charge fee accounting",
    "收入": "income revenue accounting",
    "销售": "sale income journal",
}


def graph_queries_for_failure(failure: dict) -> list[str]:
    """从失败 rollout 推断图谱搜索 query 列表。"""
    queries: list[str] = []
    case_id = failure.get("id", "")
    question = failure.get("question", "")

    for prefix, qlist in _CASE_GRAPH_QUERIES.items():
        if case_id.startswith(prefix):
            queries.extend(qlist)
            break

    for word in ("发放", "还款", "计提", "销售", "购入", "手续费", "利息"):
        if word in question:
            for prefix, qlist in _CASE_GRAPH_QUERIES.items():
                if word in " ".join(qlist) or word in prefix:
                    queries.extend(qlist[:1])

    for check in failure.get("missed_checks", []):
        if check in _CHECK_GRAPH_QUERIES:
            queries.append(_CHECK_GRAPH_QUERIES[check])

    camel = re.findall(r"\b([A-Z][a-z]+(?:[A-Z][a-z]*)+)\b", question)
    queries.extend(camel[:2])

    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out[:6]


def trace_pairs_for_failure(failure: dict) -> list[tuple[str, str]]:
    """从失败 case 推断 trace_symbol 的 (from, to) 符号对。"""
    pairs: list[tuple[str, str]] = []
    case_id = failure.get("id", "")

    for prefix, plist in _CASE_TRACE_PAIRS.items():
        if case_id.startswith(prefix):
            pairs.extend(plist)
            break

    for ref in failure.get("context_refs") or []:
        path, symbol = _parse_ref(ref)
        if symbol:
            stem = path.rsplit("/", 1)[-1].replace(".java", "").replace(".kt", "")
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


def _parse_ref(ref: str) -> tuple[str, str]:
    ref = (ref or "").strip()
    if "#" in ref:
        p, s = ref.rsplit("#", 1)
        return p.strip(), s.strip()
    if "::" in ref:
        p, s = ref.rsplit("::", 1)
        return p.strip(), s.strip()
    return ref, ""
