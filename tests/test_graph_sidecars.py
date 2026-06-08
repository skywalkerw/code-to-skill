"""graph sidecars 与 role_index 测试。"""
from __future__ import annotations

from code_to_skill.code_graph.role_index import build_role_index
from code_to_skill.code_graph.types import CodeGraph, GraphNode, NodeKind
from code_to_skill.skillopt_loop.graph_sidecars import (
    EntrypointIndex,
    EntrypointRecord,
    EvidenceHit,
    EvidenceIndexStore,
    GraphSidecarContext,
)
from code_to_skill.skillopt_loop.code_evidence import _resolve_from_entry
from code_to_skill.skillopt_loop.rollout_helpers import assemble_rollout_user_content


def test_build_role_index_from_metadata():
    graph = CodeGraph(nodes=[
        GraphNode(
            id="a.java::fineract::accounting_processor",
            kind=NodeKind.class_,
            name="accounting_processor",
            file_path="a.java",
            metadata={"framework": "fineract", "role": "accounting_processor"},
        ),
        GraphNode(
            id="b.java::Spring::RestController",
            kind=NodeKind.route,
            name="spring:RestController",
            file_path="api/LoanApi.java",
            metadata={"framework": "spring", "annotation": "@RestController"},
        ),
    ])
    payload = build_role_index(graph)
    assert payload["entry_count"] >= 2
    roles = {e["role"] for e in payload["entries"]}
    assert "accounting_processor" in roles
    assert "api_resource" in roles


def test_entrypoint_index_resolve_rest():
    index = EntrypointIndex([
        EntrypointRecord(
            id="entry:rest:handler1",
            kind="rest",
            path="api/Foo.java",
            handler_node_id="h1",
        ),
    ])
    assert index.resolve_from_entry(file_path="api/Foo.java") == "rest"
    assert index.resolve_from_entry(file_path="other.java") == ""
    assert index.resolve_from_entry(entrypoint_id="entry:rest:handler1") == "rest"


def test_evidence_index_lookup_ref():
    store = EvidenceIndexStore([
        EvidenceHit(
            evidence_id="ev-001",
            type="trace",
            source_ref="A→B→C",
            atom_ids=["atom-1"],
        ),
    ])
    hits = store.lookup_ref("A")
    assert len(hits) == 1
    assert "trace" in store.format_hit(hits[0])


def test_resolve_from_entry_no_api_heuristic():
    ctx = GraphSidecarContext(
        entrypoints=EntrypointIndex([
            EntrypointRecord(
                id="entry:rest:x",
                kind="rest",
                path="internal/Service.java",
            ),
        ]),
    )
    assert _resolve_from_entry(ctx, file_path="internal/Service.java") == "rest"
    assert _resolve_from_entry(ctx, file_path="internal/NoEntry.java") == ""
    assert _resolve_from_entry(None, file_path="api/Foo.java") == ""


def test_graph_sidecar_resolve_graph_role():
    ctx = GraphSidecarContext(
        graph_role_hints={
            "journal_entry": {"framework": "fineract", "roles": ["accounting_processor"]},
        },
    )
    fw, role = ctx.resolve_graph_role({"task_type": "journal_entry"})
    assert fw == "fineract"
    assert role == "accounting_processor"
    _, role2 = ctx.resolve_graph_role({
        "graph_role": "api_resource",
        "graph_framework": "spring",
    })
    assert role2 == "api_resource"


def test_assemble_rollout_user_content_preserves_code():
    task = "Q" * 2000
    code = "\n--- code ---\n" + "X" * 2000
    out = assemble_rollout_user_content(
        task, code, task_limit=500, code_limit=800, total_limit=1200,
    )
    assert len(out) <= 1200
    assert "code" in out
    assert out.startswith("Q" * 500)
