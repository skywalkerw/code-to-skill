"""跨 run 持久规则库（设计 08 §9）。"""
from __future__ import annotations

import json
import logging
import re
from hashlib import sha1
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from code_to_skill.time_utils import local_timestamp

from .skill_quality import QualityGateConfig, scan_skill_quality

logger = logging.getLogger(__name__)

_RULE_HEADING = "## Rule bank (verified)"
_WEAK_RULE_IDS = {"", "rule", "rules", "c", "markdown"}


@dataclass
class RuleBankConfig:
    enabled: bool = False
    path: str = ""
    max_active_rules: int = 20
    min_support_count: int = 1
    exclude_regressed: bool = True
    write_back: bool = True
    min_support_score: float = 0.55

    @classmethod
    def from_skillopt_settings(cls, skillopt_settings: dict[str, Any] | None) -> "RuleBankConfig":
        raw = (skillopt_settings or {}).get("rule_bank") or {}
        path = str(raw.get("path") or "").strip()
        enabled = bool(raw.get("enabled", False)) and bool(path)
        return cls(
            enabled=enabled,
            path=path,
            max_active_rules=int(raw.get("max_active_rules", 20) or 20),
            min_support_count=int(raw.get("min_support_count", 1) or 1),
            exclude_regressed=bool(raw.get("exclude_regressed", True)),
            write_back=bool(raw.get("write_back", True)),
            min_support_score=float(raw.get("min_support_score", 0.55) or 0.55),
        )


def compute_rule_support_score(rule: dict) -> float:
    """设计 08 §8.3 — heuristic support from evidence and replay history."""
    support = int(rule.get("support_count", 0) or 0)
    accepted = int(rule.get("accepted_count", 0) or 0)
    regressions = int(rule.get("regression_count", 0) or 0)
    evidence_refs = rule.get("evidence_refs") or []
    code_confidence = 0.9 if evidence_refs else (0.6 if support > 0 else 0.3)
    replay_fix = min(1.0, accepted / max(support, 1))
    no_regression = 1.0 if regressions == 0 else max(0.0, 1.0 - 0.25 * regressions)
    return round(0.4 * code_confidence + 0.4 * replay_fix + 0.2 * no_regression, 3)


def _rule_text_key(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def _rule_hash(text: str) -> str:
    return sha1(_rule_text_key(text).encode("utf-8")).hexdigest()[:10]


def _is_weak_rule_id(rule_id: str) -> bool:
    rid = (rule_id or "").strip().lower()
    return rid in _WEAK_RULE_IDS or len(rid) < 2


def _normalize_rule_id(text: str) -> str:
    norm = _rule_text_key(text)
    digest = _rule_hash(norm)
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", norm).strip("_")[:40].strip("_")
    if not slug or slug in _WEAK_RULE_IDS:
        return f"rule_{digest}"
    if len(slug) < 8:
        return f"{slug}_{digest}"
    return slug


def _unique_rule_id(rule_id: str, text: str, existing_ids: set[str]) -> str:
    if rule_id not in existing_ids:
        return rule_id
    digest = _rule_hash(text)
    base = rule_id[:37].rstrip("_") or "rule"
    candidate = f"{base}_{digest}"
    if candidate not in existing_ids:
        return candidate
    n = 2
    while f"{candidate}_{n}" in existing_ids:
        n += 1
    return f"{candidate}_{n}"


def load_rules(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.is_file():
        return []
    rules: list[dict[str, Any]] = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rules.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rules


def save_rules(path: str | Path, rules: list[dict[str, Any]]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        for rule in rules:
            f.write(json.dumps(rule, ensure_ascii=False) + "\n")


def select_active_rules(rules: list[dict], config: RuleBankConfig) -> list[dict]:
    active: list[dict] = []
    for rule in rules:
        if rule.get("status") != "active":
            continue
        if config.exclude_regressed and int(rule.get("regression_count", 0) or 0) > 0:
            continue
        if int(rule.get("support_count", 0) or 0) < config.min_support_count:
            continue
        if compute_rule_support_score(rule) < config.min_support_score:
            continue
        active.append(rule)
    active.sort(key=lambda r: (-int(r.get("support_count", 0) or 0), r.get("rule_id", "")))
    return active[: config.max_active_rules]


def inject_rule_bank_into_skill(skill: str, rules: list[dict], config: RuleBankConfig) -> str:
    """Prepend verified rules block without duplicating existing bullets."""
    if not config.enabled or not rules:
        return skill
    active = select_active_rules(rules, config)
    if not active:
        return skill
    lines = [f"- {r['text']}" for r in active if r.get("text")]
    if not lines:
        return skill
    block = _RULE_HEADING + "\n\n" + "\n".join(lines)
    if _RULE_HEADING in skill:
        return skill
    # skip bullets already present verbatim
    new_lines = [ln for ln in lines if ln.lstrip("- ").strip() not in skill]
    if not new_lines:
        return skill
    block = _RULE_HEADING + "\n\n" + "\n".join(new_lines)
    return block + "\n\n" + skill


def promote_rules_from_skill(
    skill: str,
    *,
    source_run: str,
    path: str | Path,
    quality_config: QualityGateConfig | None = None,
) -> list[dict[str, Any]]:
    """Extract bullets under the verified rule bank heading for promotion."""
    cfg = quality_config or QualityGateConfig()
    promoted: list[dict[str, Any]] = []
    all_rules = load_rules(path)
    existing = {str(r.get("rule_id")) for r in all_rules if r.get("rule_id")}
    by_text = {
        _rule_text_key(str(r.get("text") or "")): r
        for r in all_rules
        if _rule_text_key(str(r.get("text") or ""))
    }
    changed = False
    candidates: list[str] = []
    if _RULE_HEADING not in skill:
        return promoted
    section = skill.split(_RULE_HEADING, 1)[1]
    for ln in section.splitlines():
        stripped = ln.strip()
        if stripped.startswith("## "):
            break
        if stripped.startswith("- "):
            candidates.append(stripped.lstrip("- ").strip())
    for text in candidates:
        scan = scan_skill_quality(f"- {text}\n", cfg)
        if not scan.get("passed"):
            continue
        text_key = _rule_text_key(text)
        existing_rule = by_text.get(text_key)
        if existing_rule is not None:
            old_rid = str(existing_rule.get("rule_id") or "")
            if _is_weak_rule_id(old_rid):
                new_rid = _unique_rule_id(_normalize_rule_id(text), text, existing)
                existing.discard(old_rid)
                existing.add(new_rid)
                existing_rule["rule_id"] = new_rid
                changed = True
            runs = list(existing_rule.get("source_runs") or [])
            if source_run and source_run not in runs:
                runs.append(source_run)
                existing_rule["source_runs"] = runs[-8:]
                existing_rule["last_seen_at"] = local_timestamp()
                changed = True
            continue
        rid = _unique_rule_id(_normalize_rule_id(text), text, existing)
        promoted.append({
            "rule_id": rid,
            "text": text,
            "source_runs": [source_run],
            "source_items": [],
            "evidence_refs": [],
            "support_count": 1,
            "accepted_count": 1,
            "regression_count": 0,
            "last_seen_at": local_timestamp(),
            "status": "active",
        })
        existing.add(rid)
        by_text[text_key] = promoted[-1]
    if promoted:
        all_rules.extend(promoted)
        changed = True
    if changed:
        save_rules(path, all_rules)
    return promoted


def warm_start_rule_bank(
    skill_path: str | Path,
    *,
    rule_bank_path: str | Path,
    source_run: str = "warm_start",
    quality_config: QualityGateConfig | None = None,
) -> list[dict[str, Any]]:
    """Promote rules from a prior best_skill into the rule bank (idempotent)."""
    p = Path(skill_path)
    if not p.is_file():
        return []
    return promote_rules_from_skill(
        p.read_text(encoding="utf-8"),
        source_run=source_run,
        path=rule_bank_path,
        quality_config=quality_config,
    )


def record_rule_bank_acceptance(
    skill: str,
    path: str | Path,
    *,
    source_run: str,
    step: int,
    replay_fixed_ids: list[str] | None = None,
) -> int:
    """Bump counters for active rules present in accepted skill."""
    rules = load_rules(path)
    if not rules:
        return 0
    fixed = set(replay_fixed_ids or [])
    touched = 0
    for rule in rules:
        text = str(rule.get("text") or "").strip()
        if not text or text not in skill:
            continue
        rule["support_count"] = int(rule.get("support_count", 0) or 0) + 1
        rule["accepted_count"] = int(rule.get("accepted_count", 0) or 0) + 1
        rule["last_seen_at"] = local_timestamp()
        rule["last_accept_step"] = step
        runs = list(rule.get("source_runs") or [])
        if source_run and source_run not in runs:
            runs.append(source_run)
        rule["source_runs"] = runs[-8:]
        exemplars = set(rule.get("source_items") or [])
        exemplars.update(i for i in fixed if i)
        rule["source_items"] = sorted(exemplars)[:24]
        touched += 1
    if touched:
        save_rules(path, rules)
    return touched


def remove_no_evidence_business_rules(
    candidate_rules: list[dict],
) -> list[dict]:
    """过滤无代码证据的业务规则（设计 09 §9）。

    保留：
    - prompt_echo / output_format_error 类型的规则（不需要代码证据）
    - 有 evidence_refs 或 code_facts 的业务规则

    移除：
    - missing_business_rule 类型但无 evidence_refs 的规则
    """
    filtered: list[dict] = []
    removed = 0
    for rule in candidate_rules:
        failure_type = str(rule.get("failure_type") or "")
        evidence_refs = rule.get("evidence_refs") or []
        code_facts = rule.get("code_facts") or []

        if failure_type == "missing_business_rule":
            if not evidence_refs and not code_facts:
                logger.debug(
                    "rule_bank: removing business rule without code evidence: %s",
                    str(rule.get("rule_id") or rule.get("text", "")[:60]),
                )
                removed += 1
                continue
        filtered.append(rule)

    if removed:
        logger.info("rule_bank: removed %d business rules without code evidence", removed)
    return filtered


def upsert_candidate_rules(
    candidate_rules: list[dict],
    path: str | Path,
    *,
    source_run: str,
    step: int,
    accepted_item_ids: list[str] | None = None,
) -> int:
    """Persist newly accepted diagnosis candidate rules into the rule bank。

    前置检查：无证据的业务规则会被过滤（设计 09 §9）。
    """
    if not candidate_rules:
        return 0
    candidate_rules = remove_no_evidence_business_rules(candidate_rules)
    rules = load_rules(path)
    by_id = {str(rule.get("rule_id")): rule for rule in rules if rule.get("rule_id")}
    by_text = {
        _rule_text_key(str(rule.get("text") or "")): rule
        for rule in rules
        if _rule_text_key(str(rule.get("text") or ""))
    }
    existing_ids = set(by_id)
    accepted = {str(i) for i in (accepted_item_ids or []) if i}
    changed = 0
    for candidate in candidate_rules:
        text = str(candidate.get("text") or "").strip()
        if not text:
            continue
        supplied_rid = str(candidate.get("rule_id") or "").strip()
        rid = supplied_rid if supplied_rid and not _is_weak_rule_id(supplied_rid) else _normalize_rule_id(text)
        text_key = _rule_text_key(text)
        source_item = str(candidate.get("source_item") or "").strip()
        if accepted and source_item and source_item not in accepted:
            continue
        existing = by_text.get(text_key) or by_id.get(rid)
        if existing is None:
            rid = _unique_rule_id(rid, text, existing_ids)
            existing = {
                "rule_id": rid,
                "text": text,
                "source_runs": [],
                "source_items": [],
                "evidence_refs": [],
                "support_count": 0,
                "accepted_count": 0,
                "regression_count": 0,
                "status": "active",
            }
            rules.append(existing)
            by_id[rid] = existing
            by_text[text_key] = existing
            existing_ids.add(rid)
        elif _is_weak_rule_id(str(existing.get("rule_id") or "")):
            new_rid = _unique_rule_id(_normalize_rule_id(text), text, existing_ids)
            old_rid = str(existing.get("rule_id") or "")
            existing["rule_id"] = new_rid
            by_id.pop(old_rid, None)
            by_id[new_rid] = existing
            existing_ids.add(new_rid)
        runs = list(existing.get("source_runs") or [])
        if source_run and source_run not in runs:
            runs.append(source_run)
        source_items = set(existing.get("source_items") or [])
        if source_item:
            source_items.add(source_item)
        evidence_refs = set(existing.get("evidence_refs") or [])
        evidence_refs.update(str(ref) for ref in (candidate.get("evidence_refs") or []) if ref)
        existing["source_runs"] = runs[-8:]
        existing["source_items"] = sorted(source_items)[:24]
        existing["evidence_refs"] = sorted(evidence_refs)[:24]
        existing["support_count"] = int(existing.get("support_count", 0) or 0) + 1
        existing["accepted_count"] = int(existing.get("accepted_count", 0) or 0) + 1
        existing["last_seen_at"] = local_timestamp()
        existing["last_accept_step"] = step
        existing["status"] = existing.get("status") or "active"
        changed += 1
    if changed:
        save_rules(path, rules)
    return changed


def record_rule_bank_regression(
    path: str | Path,
    regressed_ids: list[str],
) -> int:
    """Increment regression_count for rules tied to replay regressions."""
    if not regressed_ids:
        return 0
    regressed = set(regressed_ids)
    rules = load_rules(path)
    touched = 0
    for rule in rules:
        items = set(rule.get("source_items") or [])
        if not items.intersection(regressed):
            continue
        rule["regression_count"] = int(rule.get("regression_count", 0) or 0) + 1
        rule["last_seen_at"] = local_timestamp()
        touched += 1
    if touched:
        save_rules(path, rules)
    return touched


def should_record_rule_bank_regression(
    *,
    action: str,
    replay_report: dict[str, Any] | None,
    accepted_candidate_hash: str,
    accepted_actions: set[str] | None = None,
) -> bool:
    """Return true only when the accepted skill itself caused replay regression.

    Replay gate may evaluate a candidate and then reject it or downgrade the
    decision to the current skill. In those cases the persistent rule bank must
    not inherit the rejected candidate's regression.
    """
    if not replay_report or not replay_report.get("regressed_ids"):
        return False
    actions = accepted_actions or {
        "accept_new_best",
        "accept",
        "accept_new_best_from_knowledge",
        "accept_current_knowledge",
    }
    if action not in actions:
        return False
    replay_hash = str(replay_report.get("candidate_hash") or "")
    if replay_hash and replay_hash != accepted_candidate_hash:
        return False
    return True
