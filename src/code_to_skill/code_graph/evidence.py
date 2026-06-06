"""图谱证据构建 — 为 M3 SkillAtom 补充 edge_path 与 evidence_index。"""
from __future__ import annotations

import json
import os
import re
from typing import Any

from code_to_skill.atom_extractor.types import EvidenceIndexEntry, SkillAtom, SourceRef

from .graph_queries import GraphQueryEngine


class EvidenceBuilder:
    """基于 graph.db 为 atom 的 code source_refs 补充调用链证据。"""

    def __init__(self, db_path: str, repo_root: str = ""):
        if not db_path or not os.path.isfile(db_path):
            raise FileNotFoundError(f"graph.db not found: {db_path}")
        self.engine = GraphQueryEngine.from_path(db_path)
        self.repo_root = repo_root

    def enrich_atoms(self, atoms: list[SkillAtom]) -> list[SkillAtom]:
        """为每个 atom 的 code source_ref 填充 edge_path（callees 一层）。"""
        enriched: list[SkillAtom] = []
        for atom in atoms:
            enriched.append(self._enrich_one(atom))
        return enriched

    def _enrich_one(self, atom: SkillAtom) -> SkillAtom:
        updated_refs: list[SourceRef] = []
        trace_notes: list[str] = []

        for ref in atom.source_refs:
            if ref.type != "code" or not ref.id:
                updated_refs.append(ref)
                continue
            if ref.edge_path:
                updated_refs.append(ref)
                continue

            node = self.engine.get_node(ref.id)
            if not node:
                nodes = self.engine.find_by_name(ref.id.split("::")[-1], exact=False)
                node = nodes[0] if nodes else None

            edge_path: list[str] = []
            if node:
                callees = self.engine.callees(node.id, depth=1)
                edge_path = [c["name"] for c in callees[:6] if c.get("name")]
                if edge_path:
                    trace_notes.append(f"{node.name}→{','.join(edge_path[:3])}")

            updated_refs.append(ref.model_copy(update={"edge_path": edge_path}))

        summary = atom.evidence_summary
        if trace_notes:
            prefix = f"Graph trace: {'; '.join(trace_notes[:3])}"
            summary = f"{prefix}. {summary}" if summary else prefix

        return atom.model_copy(update={
            "source_refs": updated_refs,
            "evidence_summary": summary,
        })

    def build_evidence_index(self, atoms: list[SkillAtom]) -> list[EvidenceIndexEntry]:
        """生成 evidence_index 条目（code_node / trace）。"""
        entries: list[EvidenceIndexEntry] = []
        counter = 0

        for atom in atoms:
            for ref in atom.source_refs:
                if ref.type != "code":
                    continue
                counter += 1
                entries.append(EvidenceIndexEntry(
                    evidence_id=f"ev-{counter:05d}",
                    type="code_node",
                    source_ref=ref.id,
                    atom_ids=[atom.atom_id],
                    confidence_contribution=min(0.2, atom.confidence * 0.15),
                ))
                if ref.edge_path:
                    counter += 1
                    entries.append(EvidenceIndexEntry(
                        evidence_id=f"ev-{counter:05d}",
                        type="trace",
                        source_ref="→".join(ref.edge_path),
                        atom_ids=[atom.atom_id],
                        confidence_contribution=0.05,
                    ))
        return entries

    def search_supporting_nodes(self, claim: str, limit: int = 5) -> list[dict[str, Any]]:
        """按 claim 关键词搜索支撑节点（供 LLM 抽取增强）。"""
        terms = re.findall(r"[\w\u4e00-\u9fff]{3,}", claim)
        query = " ".join(terms[:6]) if terms else claim
        return self.engine.search(query, limit=limit)
