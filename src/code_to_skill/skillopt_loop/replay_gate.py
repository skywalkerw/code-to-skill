"""Replay gate — 防回归（设计 08 §12）。"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .output_hygiene import OutputHygieneConfig, apply_hygiene_to_rollout_results

logger = logging.getLogger(__name__)

_REPLAY_ITEM_FIELDS = (
    "question",
    "input",
    "expected_checks",
    "context_refs",
    "context_mode",
    "task_type",
    "response_mode",
    "reflect_focus",
    "check_aliases",
    "item_check_aliases",
    "scorer",
    "scorer_config",
    "score_script",
    "scorer_script",
    "score_timeout_seconds",
    "_benchmark_dir",
    "_item_dir",
)


@dataclass
class ReplayGateConfig:
    enabled: bool = True
    pool_max_items: int = 12
    min_hard_pass_rate: float = 1.0
    reject_on_prompt_echo: bool = True
    reject_on_regression: bool = True
    on_regression: str = "reject"
    include_rule_exemplars: bool = True
    include_prompt_echo_cases: bool = True
    external_pool_paths: list[str] = field(default_factory=list)
    pool_path: str = ""

    @classmethod
    def from_skillopt_settings(cls, skillopt_settings: dict[str, Any] | None) -> "ReplayGateConfig":
        raw = (skillopt_settings or {}).get("replay_gate") or {}
        return cls(
            enabled=bool(raw.get("enabled", True)),
            pool_max_items=int(raw.get("pool_max_items", raw.get("max_items", 12)) or 12),
            min_hard_pass_rate=float(raw.get("min_hard_pass_rate", 1.0) or 1.0),
            reject_on_prompt_echo=bool(raw.get("reject_on_prompt_echo", True)),
            reject_on_regression=bool(
                raw.get("reject_on_regression", raw.get("reject_on_active_rule_regression", True))
            ),
            on_regression=str(raw.get("on_regression", "reject") or "reject"),
            include_rule_exemplars=bool(raw.get("include_rule_exemplars", True)),
            include_prompt_echo_cases=bool(raw.get("include_prompt_echo_cases", True)),
            external_pool_paths=[
                str(p) for p in (raw.get("external_pool_paths") or []) if p
            ],
            pool_path=str(raw.get("pool_path", "") or ""),
        )


def default_pool_path(output_dir: str) -> str:
    return str(Path(output_dir) / "replay_pool.json")


def load_replay_pool(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    items = data.get("items") if isinstance(data, dict) else data
    return list(items) if isinstance(items, list) else []


def save_replay_pool(path: str | Path, items: list[dict[str, Any]]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"schema_version": "1.0", "items": items}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def merge_rule_exemplars_into_pool(
    pool: list[dict],
    rules: list[dict],
    *,
    max_items: int = 12,
    item_registry: dict[str, dict] | None = None,
) -> list[dict]:
    """Add rule exemplars only when a complete replay item is available.

    Rule-bank ``source_items`` are IDs, not benchmark records. Replaying synthetic
    empty questions produces meaningless false regressions, so unresolved IDs are
    intentionally skipped.
    """
    by_id: dict[str, dict] = {str(x.get("id")): x for x in pool if x.get("id")}
    registry = item_registry or {}
    skipped: set[str] = set()
    for rule in rules:
        for item_id in rule.get("source_items") or []:
            iid = str(item_id)
            if not iid:
                continue
            if iid not in by_id and iid in registry:
                source = dict(registry[iid])
                by_id[iid] = {
                    **source,
                    "id": iid,
                    "input": source.get("input") or source.get("question") or "",
                    "expected_checks": source.get("expected_checks") or [],
                    "context_refs": source.get("context_refs") or [],
                    "failure_type": "rule_exemplar",
                    "baseline_hard": 1,
                }
            if iid not in by_id:
                if iid:
                    skipped.add(iid)
                continue
            by_id[iid]["from_rule_id"] = rule.get("rule_id", "")
            by_id[iid].setdefault("failure_type", "rule_exemplar")
            by_id[iid].setdefault("baseline_hard", 1)
    if skipped:
        logger.debug("Skipped %d rule exemplar IDs without replay records", len(skipped))
    merged = list(by_id.values())
    merged.sort(
        key=lambda x: (
            0 if int(x.get("baseline_hard", 0) or 0) == 0 else 1,
            -int(x.get("last_step") or 0),
        ),
    )
    return merged[:max_items]


def update_replay_pool(
    pool: list[dict],
    rollout_results: list[dict],
    *,
    max_items: int = 12,
) -> list[dict]:
    """Add hard failures; dedupe by id; keep most recent."""
    by_id: dict[str, dict] = {str(x.get("id")): dict(x) for x in pool if x.get("id")}
    for row in rollout_results:
        if row.get("hard", 1) != 0:
            continue
        iid = str(row.get("id", ""))
        if not iid:
            continue
        prev = by_id.get(iid, {})
        echo = row.get("output_hygiene_reason") in ("prompt_echo", "tool_leak")
        item: dict[str, Any] = {
            "id": iid,
            "input": row.get("input") or row.get("question") or prev.get("input", ""),
            "expected_checks": row.get("expected_checks") or prev.get("expected_checks") or [],
            "context_refs": row.get("context_refs") or prev.get("context_refs") or [],
            "failure_type": row.get("output_hygiene_reason") or row.get("fail_reason", ""),
            "last_step": row.get("step"),
            "baseline_hard": int(prev.get("baseline_hard", 0) or 0),
            "prompt_echo": echo or bool(prev.get("prompt_echo")),
        }
        for field in _REPLAY_ITEM_FIELDS:
            if field in item:
                continue
            value = row.get(field)
            if value is None:
                value = prev.get(field)
            if value not in (None, ""):
                item[field] = value
        if "check_aliases" not in item and row.get("item_check_aliases"):
            item["check_aliases"] = row.get("item_check_aliases")
        by_id[iid] = item
    merged = list(by_id.values())
    merged.sort(
        key=lambda x: (
            0 if x.get("prompt_echo") else 1,
            0 if int(x.get("baseline_hard", 0) or 0) == 0 else 1,
            -int(x.get("last_step") or 0),
        ),
    )
    return merged[:max_items]


def apply_replay_results_to_pool(
    pool: list[dict],
    results: list[dict],
    *,
    step: int,
) -> tuple[list[dict], list[str], list[str]]:
    """Update baseline_hard from replay; return (pool, regressed_ids, fixed_ids)."""
    by_id = {str(x.get("id")): dict(x) for x in pool if x.get("id")}
    regressed: list[str] = []
    fixed: list[str] = []
    for row in results:
        iid = str(row.get("id", ""))
        if not iid or iid not in by_id:
            continue
        entry = by_id[iid]
        was_passing = int(entry.get("baseline_hard", 0) or 0) == 1
        now_pass = int(row.get("hard", 0) or 0) == 1
        if now_pass:
            if not was_passing:
                fixed.append(iid)
            entry["baseline_hard"] = 1
            entry["last_replay_pass_step"] = step
        elif was_passing:
            regressed.append(iid)
            entry["baseline_hard"] = 0
    return list(by_id.values()), regressed, fixed


def run_replay_gate(
    skill: str,
    pool_items: list[dict],
    *,
    adapter: Any,
    target_backend: Any = None,
    hygiene_cfg: OutputHygieneConfig | None = None,
    config: ReplayGateConfig | None = None,
    step: int = 0,
    candidate_hash: str = "",
) -> dict[str, Any]:
    cfg = config or ReplayGateConfig()
    if not cfg.enabled or not pool_items or not skill.strip():
        return {
            "schema_version": "1.0",
            "passed": True,
            "reason": "disabled_or_empty",
            "hard_pass_rate": 1.0,
            "hard": 0.0,
            "soft": 0.0,
            "results": [],
            "step": step,
            "candidate_hash": candidate_hash,
            "replay_items": 0,
            "fixed_ids": [],
            "regressed_ids": [],
            "prompt_echo_ids": [],
        }
    batch = []
    for item in pool_items:
        replay_item = dict(item)
        replay_item["id"] = item.get("id", "")
        replay_item["question"] = item.get("input") or item.get("question") or ""
        replay_item["expected_checks"] = item.get("expected_checks") or []
        replay_item["context_refs"] = item.get("context_refs") or []
        if "check_aliases" not in replay_item and item.get("item_check_aliases"):
            replay_item["check_aliases"] = item.get("item_check_aliases")
        batch.append(replay_item)
    results = adapter.rollout(skill, batch, target_backend=target_backend)
    if hygiene_cfg and hygiene_cfg.enabled:
        results = apply_hygiene_to_rollout_results(results, hygiene_cfg)
    passed_n = sum(1 for r in results if r.get("hard", 0) == 1)
    total = max(len(results), 1)
    rate = passed_n / total
    soft_avg = sum(float(r.get("soft", 0)) for r in results) / total
    echo_ids = [
        str(r.get("id", ""))
        for r in results
        if r.get("output_hygiene_reason") in ("prompt_echo", "tool_leak")
    ]
    _, regressed_ids, fixed_ids = apply_replay_results_to_pool(pool_items, results, step=step)
    passed = rate >= cfg.min_hard_pass_rate
    reason = "ok"
    if cfg.reject_on_prompt_echo and echo_ids:
        passed = False
        reason = "prompt_echo_in_replay"
    elif cfg.reject_on_regression and regressed_ids:
        passed = False
        reason = "replay_regression"
        if cfg.on_regression == "accept_current":
            reason = "replay_regression_accept_current"
    elif not passed:
        reason = "hard_pass_rate_below_threshold"
    return {
        "schema_version": "1.0",
        "step": step,
        "candidate_hash": candidate_hash,
        "replay_items": len(pool_items),
        "hard": round(rate, 3),
        "soft": round(soft_avg, 3),
        "hard_pass_rate": rate,
        "passed_count": passed_n,
        "total": total,
        "prompt_echo_count": len(echo_ids),
        "prompt_echo_ids": echo_ids,
        "fixed_ids": fixed_ids,
        "regressed_ids": regressed_ids,
        "on_regression": cfg.on_regression,
        "passed": passed,
        "reason": reason,
        "results": results,
    }


def merge_external_replay_pools(
    pool: list[dict],
    paths: list[str],
    *,
    max_items: int = 12,
) -> list[dict]:
    """Merge items from prior run replay_pool.json files (cross-run failures)."""
    by_id: dict[str, dict] = {str(x.get("id")): dict(x) for x in pool if x.get("id")}
    for path in paths:
        for item in load_replay_pool(path):
            iid = str(item.get("id", ""))
            if not iid:
                continue
            prev = by_id.get(iid, {})
            merged = {**prev, **item, "id": iid}
            if not merged.get("last_step"):
                merged["external_pool"] = str(path)
            by_id[iid] = merged
    merged = list(by_id.values())
    merged.sort(
        key=lambda x: (
            0 if x.get("prompt_echo") else 1,
            0 if int(x.get("baseline_hard", 0) or 0) == 0 else 1,
            -int(x.get("last_step") or 0),
        ),
    )
    return merged[:max_items]


def filter_replay_pool(cfg: ReplayGateConfig, pool: list[dict]) -> list[dict]:
    """Apply pool inclusion flags before replay."""
    if not pool:
        return []
    out = list(pool)
    if not cfg.include_prompt_echo_cases:
        out = [x for x in out if not x.get("prompt_echo")]
    return out[: cfg.pool_max_items]


def write_replay_eval_report(output_dir: str, step: int, report: dict[str, Any]) -> str:
    import os

    payload = {k: v for k, v in report.items() if k != "results"}
    step_dir = os.path.join(output_dir, "steps", f"step_{step:04d}")
    os.makedirs(step_dir, exist_ok=True)
    path = os.path.join(step_dir, "replay_eval_report.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return path
