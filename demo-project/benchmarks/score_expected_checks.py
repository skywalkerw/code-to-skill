#!/usr/bin/env python3
"""Shared Fineract benchmark scorer — keyword checks on expected_checks.

stdin JSON::
    {"predicted": str, "item": dict, "global_check_aliases": dict | optional}

stdout JSON::
    hard, soft, passed_checks, missed_checks, justification
"""
from __future__ import annotations

import json
import sys
from typing import Any


def _normalize_text(text: str) -> str:
    return text.replace(",", "").lower()


def _check_keyword(text: str, check: str) -> bool:
    return _normalize_text(check) in _normalize_text(text)


def _merge_aliases(
    global_aliases: dict[str, list[str]] | None,
    item_aliases: dict[str, list[str]] | None,
) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {}
    for source in (global_aliases, item_aliases):
        for key, values in (source or {}).items():
            norm_key = (key or "").strip().lower()
            if not norm_key:
                continue
            merged.setdefault(norm_key, [])
            merged[norm_key].extend(str(v) for v in (values or []) if str(v).strip())
    return merged


def _aliases_for(check: str, aliases: dict[str, list[str]]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for alias in aliases.get((check or "").strip().lower(), []):
        norm = _normalize_text(alias)
        if norm and norm not in seen:
            out.append(alias)
            seen.add(norm)
    return out


def _check_expected(
    text: str,
    check: str,
    aliases: dict[str, list[str]],
) -> bool:
    if _check_keyword(text, check):
        return True
    return any(_check_keyword(text, alias) for alias in _aliases_for(check, aliases))


def score(predicted: str, item: dict, global_aliases: dict[str, Any] | None) -> dict:
    # 与 skillopt_loop.scoring.score_rollout_result 对齐：逐条 expected_checks 做子串匹配。
    checks = list(item.get("expected_checks") or [])
    # global_check_aliases 来自 settings.skillopt；item.check_aliases 可追加同义词。
    aliases = _merge_aliases(global_aliases, item.get("check_aliases"))

    passed_checks: list[str] = []
    missed_checks: list[str] = []
    for check in checks:
        if _check_expected(predicted, check, aliases):
            passed_checks.append(check)
        else:
            missed_checks.append(check)

    total = len(checks) if checks else 1
    passed = len(passed_checks)
    # soft：通过率；hard：全通过才为 1（驱动 selection gate 与 reflect 的 missed_checks）。
    soft = passed / total
    hard = 1 if soft == 1.0 else 0

    precision = passed / max(len(predicted.split()), 1)
    recall = soft
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )

    return {
        "hard": hard,
        "soft": round(soft, 3),
        "passed_checks": passed_checks,
        "missed_checks": missed_checks,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "justification": f"keyword checks {passed}/{total}",
    }


def main() -> None:
    # 由 scoring.score_with_python_script 以子进程调用；stdout 须为单行 JSON。
    payload = json.load(sys.stdin)
    predicted = str(payload.get("predicted") or "")
    item = payload.get("item") or {}
    global_aliases = payload.get("global_check_aliases")
    result = score(predicted, item, global_aliases)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
