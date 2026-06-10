"""M4 self_evolution 配置解析。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SelfEvolutionConfig:
    enabled: bool = False
    trace_pool_enabled: bool = True
    min_support_count: int = 2
    cluster_by: list[str] = field(
        default_factory=lambda: ["task_type", "missed_checks", "context_refs"],
    )
    include_success: bool = True
    include_failure: bool = True
    hierarchical_merge: bool = True
    max_merge_fan_in: int = 8
    strict_improvement: bool = True
    reject_ties: bool = True
    allowed_regressions: int = 0
    frontier_enabled: bool = False
    frontier_size: int = 3
    max_edits_per_step: int | None = None
    max_new_rules_per_step: int = 2
    max_skill_tokens: int = 2000
    hygiene_enabled: bool = True
    hygiene_each_epoch: bool = True
    min_rule_use_count: int = 1
    max_rules: int = 40
    attribution_enabled: bool = True
    inject_rule_ids: bool = True
    knowledge_merge_enabled: bool = True
    knowledge_gate_tolerance: float = 0.05
    knowledge_min_support_count: int = 2
    success_ignore_checks: list[str] = field(default_factory=list)
    success_default_checks_text: str = "verified task-specific requirements"
    success_rule_tail: str = (
        "preserve the answer pattern that passed the checks, keep variable "
        "values grounded in the user input or provided context, and make the "
        "result directly verifiable"
    )

    @classmethod
    def from_dict(
        cls,
        raw: dict[str, Any] | None,
        *,
        cli_enabled: bool = False,
        trace_merge_only: bool = False,
    ) -> "SelfEvolutionConfig":
        if not raw:
            cfg = cls(enabled=cli_enabled or trace_merge_only)
            if trace_merge_only and not cli_enabled:
                return cls._as_trace_merge_only(cfg)
            return cfg
        tp = raw.get("trace_pool") or {}
        prop = raw.get("proposals") or {}
        gate = raw.get("gate") or {}
        edits = raw.get("edits") or {}
        hygiene = raw.get("hygiene") or {}
        attr = raw.get("attribution") or {}
        knowledge = raw.get("knowledge") or {}
        cfg = cls(
            enabled=bool(raw.get("enabled", False) or cli_enabled or trace_merge_only),
            trace_pool_enabled=bool(tp.get("enabled", True)),
            min_support_count=int(tp.get("min_support_count", 2)),
            cluster_by=list(tp.get("cluster_by") or ["task_type", "missed_checks", "context_refs"]),
            include_success=bool(prop.get("include_success", True)),
            include_failure=bool(prop.get("include_failure", True)),
            hierarchical_merge=bool(prop.get("hierarchical_merge", True)),
            max_merge_fan_in=int(prop.get("max_merge_fan_in", 8)),
            strict_improvement=bool(gate.get("strict_improvement", True)),
            reject_ties=bool(gate.get("reject_ties", True)),
            allowed_regressions=int(gate.get("allowed_regressions", 0)),
            frontier_enabled=bool(gate.get("frontier_enabled", False)),
            frontier_size=int(gate.get("frontier_size", 3)),
            max_edits_per_step=edits.get("max_edits_per_step"),
            max_new_rules_per_step=int(edits.get("max_new_rules_per_step", 2)),
            max_skill_tokens=int(edits.get("max_skill_tokens", 2000)),
            hygiene_enabled=bool(hygiene.get("enabled", True)),
            hygiene_each_epoch=bool(hygiene.get("run_each_epoch", True)),
            min_rule_use_count=int(hygiene.get("min_rule_use_count", 1)),
            max_rules=int(hygiene.get("max_rules", 40)),
            attribution_enabled=bool(attr.get("enabled", True)),
            inject_rule_ids=bool(attr.get("inject_rule_ids", True)),
            knowledge_merge_enabled=bool(knowledge.get("enabled", True)),
            knowledge_gate_tolerance=float(knowledge.get("gate_tolerance", 0.05)),
            knowledge_min_support_count=int(
                knowledge.get("min_support_count", tp.get("min_support_count", 2))
            ),
            success_ignore_checks=[
                str(c).strip()
                for c in (prop.get("success_ignore_checks") or [])
                if str(c).strip()
            ],
            success_default_checks_text=str(
                prop.get("success_default_checks_text")
                or cls.success_default_checks_text
            ).strip(),
            success_rule_tail=str(
                prop.get("success_rule_tail") or cls.success_rule_tail
            ).strip(),
        )
        if trace_merge_only and not cli_enabled:
            return cls._as_trace_merge_only(cfg)
        return cfg

    @staticmethod
    def _as_trace_merge_only(cfg: "SelfEvolutionConfig") -> "SelfEvolutionConfig":
        """``--trace-merge``：仅 trace pool + proposals，不启用严格 gate / 归因。"""
        cfg.enabled = True
        cfg.strict_improvement = False
        cfg.reject_ties = False
        cfg.inject_rule_ids = False
        cfg.attribution_enabled = False
        cfg.frontier_enabled = False
        cfg.hygiene_each_epoch = False
        cfg.knowledge_merge_enabled = True
        return cfg
