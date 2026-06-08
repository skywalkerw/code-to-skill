"""M1/M3 sidecar 加载：entrypoints、role_index、evidence_index（M4 精确证据）。"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from code_to_skill.cli.pipeline_config import PipelineArtifacts, PipelineSettings


@dataclass
class EntrypointRecord:
    id: str
    kind: str
    handler_node_id: str = ""
    path: str = ""


class EntrypointIndex:
    """entrypoints.json sidecar 索引。"""

    def __init__(self, records: list[EntrypointRecord]):
        self._by_id = {r.id: r for r in records}
        self._by_path: dict[str, list[EntrypointRecord]] = {}
        for r in records:
            if r.path:
                self._by_path.setdefault(r.path, []).append(r)

    @classmethod
    def load(cls, path: str) -> "EntrypointIndex | None":
        if not path or not os.path.isfile(path):
            return None
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        records = [
            EntrypointRecord(
                id=item.get("id", ""),
                kind=item.get("kind", ""),
                handler_node_id=item.get("handler_node_id", ""),
                path=item.get("path", ""),
            )
            for item in raw
            if isinstance(item, dict) and item.get("id")
        ]
        return cls(records)

    def lookup(self, entrypoint_id: str) -> EntrypointRecord | None:
        return self._by_id.get(entrypoint_id)

    def resolve_from_entry(
        self,
        *,
        file_path: str = "",
        symbol: str = "",
        entrypoint_id: str = "",
    ) -> str:
        """解析 trace_symbol 的 ``from_entry`` 参数（禁止路径含 api 的启发式）。"""
        if entrypoint_id:
            ep = self.lookup(entrypoint_id)
            if ep:
                return ep.kind or ep.id.replace("entry:", "", 1)

        candidates: list[EntrypointRecord] = []
        if file_path:
            candidates.extend(self._by_path.get(file_path, []))
            base = os.path.basename(file_path)
            for path, eps in self._by_path.items():
                if os.path.basename(path) == base and path not in {file_path}:
                    candidates.extend(eps)

        if symbol:
            for ep in self._by_id.values():
                if symbol in ep.handler_node_id or symbol in ep.id:
                    candidates.append(ep)

        for kind in ("rest", "rpc", "cli", "job"):
            for ep in candidates:
                if ep.kind == kind:
                    return kind

        if candidates:
            return candidates[0].kind or ""
        return ""


@dataclass
class RoleIndexEntry:
    framework: str
    role: str
    file_path: str
    symbols: list[str] = field(default_factory=list)


class RoleIndex:
    """role_index.json sidecar 索引。"""

    def __init__(self, entries: list[RoleIndexEntry]):
        self._by_role: dict[str, list[RoleIndexEntry]] = {}
        self._by_framework_role: dict[tuple[str, str], list[RoleIndexEntry]] = {}
        for e in entries:
            self._by_role.setdefault(e.role, []).append(e)
            self._by_framework_role.setdefault((e.framework, e.role), []).append(e)

    @classmethod
    def load(cls, path: str) -> "RoleIndex | None":
        if not path or not os.path.isfile(path):
            return None
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        entries = [
            RoleIndexEntry(
                framework=item.get("framework", ""),
                role=item.get("role", ""),
                file_path=item.get("file_path", ""),
                symbols=list(item.get("symbols") or []),
            )
            for item in payload.get("entries", [])
            if isinstance(item, dict) and item.get("role")
        ]
        return cls(entries)

    def lookup(
        self,
        role: str,
        *,
        framework: str = "",
        limit: int = 4,
    ) -> list[RoleIndexEntry]:
        if framework:
            hits = list(self._by_framework_role.get((framework, role), []))
        else:
            hits = list(self._by_role.get(role, []))
        return hits[:limit]


@dataclass
class EvidenceHit:
    evidence_id: str
    type: str
    source_ref: str
    atom_ids: list[str] = field(default_factory=list)


class EvidenceIndexStore:
    """atoms/evidence_index.json 精确命中（禁止全文泛搜）。"""

    def __init__(self, entries: list[EvidenceHit]):
        self._by_ref: dict[str, list[EvidenceHit]] = {}
        self._by_atom: dict[str, list[EvidenceHit]] = {}
        for e in entries:
            if e.source_ref:
                self._by_ref.setdefault(e.source_ref.strip(), []).append(e)
                for part in e.source_ref.replace("→", "#").split("#"):
                    part = part.strip()
                    if part:
                        self._by_ref.setdefault(part, []).append(e)
            for aid in e.atom_ids:
                self._by_atom.setdefault(aid, []).append(e)

    @classmethod
    def load(cls, path: str) -> "EvidenceIndexStore | None":
        if not path or not os.path.isfile(path):
            return None
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        entries = [
            EvidenceHit(
                evidence_id=item.get("evidence_id", ""),
                type=item.get("type", ""),
                source_ref=item.get("source_ref", ""),
                atom_ids=list(item.get("atom_ids") or []),
            )
            for item in raw
            if isinstance(item, dict)
        ]
        return cls(entries)

    def lookup_ref(self, ref: str) -> list[EvidenceHit]:
        ref = (ref or "").strip()
        if not ref:
            return []
        hits = list(self._by_ref.get(ref, []))
        if "#" in ref:
            _, sym = ref.rsplit("#", 1)
            hits.extend(self._by_ref.get(sym.strip(), []))
        if "::" in ref:
            hits.extend(self._by_ref.get(ref.split("::")[-1], []))
        seen: set[str] = set()
        out: list[EvidenceHit] = []
        for h in hits:
            if h.evidence_id not in seen:
                seen.add(h.evidence_id)
                out.append(h)
        return out[:4]

    def lookup_atom(self, atom_id: str) -> list[EvidenceHit]:
        return list(self._by_atom.get(atom_id, []))[:4]

    @staticmethod
    def format_hit(hit: EvidenceHit) -> str:
        if hit.type == "trace":
            return f"**Evidence[{hit.evidence_id}]** call trace: `{hit.source_ref}`"
        return (
            f"**Evidence[{hit.evidence_id}]** code ref: `{hit.source_ref}` "
            f"(atoms: {', '.join(hit.atom_ids[:3])})"
        )


@dataclass
class GraphSidecarContext:
    """M4 可用的图谱 sidecar 集合。"""

    entrypoints: EntrypointIndex | None = None
    role_index: RoleIndex | None = None
    evidence_index: EvidenceIndexStore | None = None
    graph_role_hints: dict[str, Any] = field(default_factory=dict)
    use_entrypoints: bool = True
    use_role_index: bool = True
    use_evidence_index: bool = True

    @classmethod
    def from_artifacts(
        cls,
        artifacts: PipelineArtifacts,
        pipeline: PipelineSettings,
        *,
        graph_role_hints: dict | None = None,
    ) -> "GraphSidecarContext":
        entrypoints = None
        role_index = None
        for g in artifacts.graphs:
            if pipeline.use_entrypoints and g.entrypoints.present:
                entrypoints = EntrypointIndex.load(g.entrypoints.path)
            if pipeline.use_role_index and g.role_index.present:
                role_index = RoleIndex.load(g.role_index.path)
            if entrypoints or role_index:
                break

        evidence_index = None
        if pipeline.use_evidence_index and artifacts.evidence_index.present:
            evidence_index = EvidenceIndexStore.load(artifacts.evidence_index.path)

        return cls(
            entrypoints=entrypoints,
            role_index=role_index,
            evidence_index=evidence_index,
            graph_role_hints=graph_role_hints or {},
            use_entrypoints=pipeline.use_entrypoints,
            use_role_index=pipeline.use_role_index,
            use_evidence_index=pipeline.use_evidence_index,
        )

    def resolve_graph_role(self, item: dict) -> tuple[str, str]:
        """从 benchmark item 或 project hints 解析 (framework, role)。"""
        role = str(item.get("graph_role") or "").strip()
        framework = str(item.get("graph_framework") or "").strip()
        if role:
            return framework, role

        task_type = str(item.get("task_type") or "").strip()
        hints = self.graph_role_hints.get(task_type) or {}
        if isinstance(hints, dict):
            fw = str(hints.get("framework") or "")
            roles = hints.get("roles") or []
            if roles:
                return fw, str(roles[0])
            return fw, str(hints.get("role") or "")
        return "", ""
