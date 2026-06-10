"""M4 self_evolution — Trace Pool：标准化 rollout 轨迹与聚类。"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from typing import Any

from .self_evolution_config import SelfEvolutionConfig


def _trace_record(
    step: int,
    result: dict,
    skill_version: str,
) -> dict[str, Any]:
    item_id = result.get("id", "")
    return {
        "trace_id": f"step_{step:04d}:item_{item_id}",
        "step": step,
        "item_id": item_id,
        "task_type": result.get("task_type", ""),
        "hard": result.get("hard", 0),
        "soft": result.get("soft", 0.0),
        "question": (result.get("question") or "")[:500],
        "expected_checks": list(result.get("expected_checks") or []),
        "passed_checks": list(result.get("passed_checks") or []),
        "missed_checks": list(result.get("missed_checks") or []),
        "context_refs": list(result.get("context_refs") or []),
        "code_evidence_ids": list(result.get("code_evidence_ids") or []),
        "tool_calls": list(result.get("tool_calls") or []),
        "predicted_answer": (result.get("predicted_answer") or "")[:800],
        "fail_reason": (result.get("fail_reason") or "")[:200],
        "skill_version": skill_version,
    }


def _cluster_key(trace: dict, cluster_by: list[str]) -> tuple:
    parts: list[str] = []
    for dim in cluster_by:
        if dim == "task_type":
            parts.append(trace.get("task_type") or "_")
        elif dim == "missed_checks":
            missed = trace.get("missed_checks") or []
            parts.append("|".join(sorted(missed)) or "_")
        elif dim == "context_refs":
            refs = trace.get("context_refs") or []
            parts.append("|".join(sorted(refs[:3])) or "_")
        elif dim == "fail_reason":
            parts.append((trace.get("fail_reason") or "_")[:80])
        else:
            parts.append("_")
    return tuple(parts)


class TracePoolManager:
    """维护 trace_pool/ 产物。"""

    def __init__(self, output_dir: str, config: SelfEvolutionConfig):
        self.output_dir = os.path.join(output_dir, "trace_pool")
        self.config = config
        os.makedirs(self.output_dir, exist_ok=True)
        self._traces_path = os.path.join(self.output_dir, "traces.jsonl")

    def record_batch(
        self,
        step: int,
        results: list[dict],
        skill_version: str,
    ) -> list[dict]:
        if not self.config.trace_pool_enabled:
            return []
        records = [_trace_record(step, r, skill_version) for r in results]
        with open(self._traces_path, "a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return records

    def load_all_traces(self) -> list[dict]:
        if not os.path.isfile(self._traces_path):
            return []
        traces: list[dict] = []
        with open(self._traces_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    traces.append(json.loads(line))
        return traces

    def cluster_traces(
        self,
        traces: list[dict] | None = None,
        *,
        step: int | None = None,
    ) -> tuple[list[dict], dict]:
        traces = traces if traces is not None else self.load_all_traces()
        if step is not None:
            traces = [t for t in traces if t.get("step") == step]
        if not traces:
            return [], {"total_traces": 0, "clusters": 0}

        buckets: dict[tuple, list[dict]] = defaultdict(list)
        for t in traces:
            if t.get("hard", 0) == 0:
                key = _cluster_key(t, self.config.cluster_by)
                buckets[key].append(t)

        clusters: list[dict] = []
        for idx, (key, members) in enumerate(sorted(buckets.items(), key=lambda x: -len(x[1]))):
            sample = members[0]
            missed = sorted({c for m in members for c in (m.get("missed_checks") or [])})
            cluster_id = f"cluster-{idx:03d}-{'-'.join(k for k in key if k and k != '_')[:40]}"
            clusters.append({
                "cluster_id": cluster_id,
                "cluster_key": list(key),
                "support_count": len(members),
                "task_type": sample.get("task_type", ""),
                "missed_checks": missed,
                "context_refs": list(sample.get("context_refs") or []),
                "trace_ids": [m["trace_id"] for m in members],
                "sample_item_ids": [m["item_id"] for m in members[:5]],
                "avg_soft": sum(m.get("soft", 0) for m in members) / len(members),
            })

        summary = {
            "total_traces": len(traces),
            "failure_traces": sum(1 for t in traces if t.get("hard", 0) == 0),
            "clusters": len(clusters),
            "step_filter": step,
        }
        return clusters, summary

    def persist_clusters(self, clusters: list[dict], summary: dict, *, step: int | None = None) -> str:
        payload = {"clusters": clusters, "summary": summary}
        if step is not None:
            payload["step"] = step
        path = os.path.join(self.output_dir, "clusters.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        with open(os.path.join(self.output_dir, "cluster_summary.json"), "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        return path
