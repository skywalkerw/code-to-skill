"""Phase 4：框架解析与证据索引测试。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_to_skill.atom_extractor.types import SkillAtom, SourceRef
from code_to_skill.code_graph import run_code_graph_pipeline
from code_to_skill.code_graph.evidence import EvidenceBuilder
from code_to_skill.code_graph.types import EdgeKind


def _write_spring_repo(root: Path) -> None:
    pkg = root / "com" / "example"
    pkg.mkdir(parents=True)
    (pkg / "PaymentService.java").write_text(
        """package com.example;

public class PaymentService {
    public void charge(String id) {}
}
""",
        encoding="utf-8",
    )
    (pkg / "OrderService.java").write_text(
        """package com.example;

import org.springframework.stereotype.Service;
import org.springframework.beans.factory.annotation.Autowired;

@Service
public class OrderService extends BaseService implements Auditable {
    @Autowired
    private PaymentService paymentService;

    public void placeOrder(String id) {
        paymentService.charge(id);
    }
}
""",
        encoding="utf-8",
    )
    (pkg / "BaseService.java").write_text(
        "package com.example;\npublic class BaseService {}\n",
        encoding="utf-8",
    )
    (pkg / "Auditable.java").write_text(
        "package com.example;\npublic interface Auditable {}\n",
        encoding="utf-8",
    )
    mapper_dir = root / "mapper"
    mapper_dir.mkdir()
    (mapper_dir / "OrderMapper.java").write_text(
        """package mapper;

import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Select;

@Mapper
public interface OrderMapper {
    @Select("SELECT 1")
    int count();
}
""",
        encoding="utf-8",
    )


@pytest.fixture
def spring_graph(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_spring_repo(repo)
    out = tmp_path / "out"
    result = run_code_graph_pipeline(
        repo_root=str(repo),
        include=["**/*.java"],
        output_root=str(out),
        use_cache=True,
    )
    return str(repo), str(out / "graph.db"), result["graph"]


class TestFrameworkEdges:
    def test_extends_implements(self, spring_graph):
        _, _, graph = spring_graph
        kinds = {(e.source.split("::")[-1], e.target.split("::")[-1], e.kind) for e in graph.edges}
        assert any(k[2] == EdgeKind.extends for k in kinds)
        assert any(k[2] == EdgeKind.implements for k in kinds)

    def test_autowired_reference(self, spring_graph):
        _, _, graph = spring_graph
        refs = [
            e for e in graph.edges
            if e.kind == EdgeKind.references
            and "OrderService" in e.source
            and "PaymentService" in e.target
        ]
        assert refs

    def test_mybatis_mapper_node(self, spring_graph):
        _, _, graph = spring_graph
        mappers = [n for n in graph.nodes if "MyBatis" in n.id or n.metadata.get("framework") == "mybatis"]
        assert mappers


class TestEvidenceBuilder:
    def test_enrich_edge_path(self, spring_graph):
        repo_root, db_path, _ = spring_graph
        atom = SkillAtom(
            atom_id="test.charge",
            kind="procedure",
            claim="订单调用支付扣款",
            source_refs=[SourceRef(type="code", id="com/example/OrderService.java::placeOrder")],
        )
        builder = EvidenceBuilder(db_path, repo_root)
        enriched = builder.enrich_atoms([atom])[0]
        code_ref = enriched.source_refs[0]
        assert code_ref.edge_path
        assert "charge" in code_ref.edge_path

    def test_evidence_index(self, spring_graph):
        repo_root, db_path, _ = spring_graph
        atom = SkillAtom(
            atom_id="test.charge",
            kind="procedure",
            claim="订单支付",
            source_refs=[SourceRef(type="code", id="com/example/OrderService.java::placeOrder")],
        )
        builder = EvidenceBuilder(db_path, repo_root)
        enriched = builder.enrich_atoms([atom])
        index = builder.build_evidence_index(enriched)
        assert index
        assert any(e.type == "trace" for e in index)
