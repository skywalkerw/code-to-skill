"""会计 benchmark → 图谱查询/调用链映射测试。"""
from __future__ import annotations

from code_to_skill.skillopt_loop.accounting_linker import (
    graph_queries_for_failure,
    trace_pairs_for_failure,
)


def test_graph_queries_for_disburse():
    qs = graph_queries_for_failure({
        "id": "jv_loan_disburse_001",
        "question": "发放贷款",
        "missed_checks": ["贷款"],
    })
    assert any("disburse" in q.lower() or "loan" in q.lower() for q in qs)


def test_trace_pairs_for_disburse():
    pairs = trace_pairs_for_failure({
        "id": "jv_loan_disburse_001",
        "question": "发放贷款",
        "context_refs": [
            "fineract-provider/.../JournalEntriesApiResource.java#createJournalEntriesForDisbursements",
        ],
    })
    assert pairs
    assert any("createJournalEntries" in b for _, b in pairs)


def test_trace_pairs_from_context_ref():
    pairs = trace_pairs_for_failure({
        "id": "jv_custom_001",
        "context_refs": ["com/example/FooService.java#processPayment"],
    })
    assert ("FooService", "processPayment") in pairs
