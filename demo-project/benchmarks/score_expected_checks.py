#!/usr/bin/env python3
"""Shared Fineract benchmark scorer — keyword checks with no-voucher handling.

stdin JSON::
    {"predicted": str, "item": dict, "global_check_aliases": dict | optional}

stdout JSON::
    hard, soft, passed_checks, missed_checks, justification
"""
from __future__ import annotations

import json
import sys
from typing import Any

# 回答明确不生成会计凭证时，借贷类 expected_checks 不应触发平衡验算。
_BALANCE_VERIFY_CHECKS = frozenset({"借贷平衡", "借贷校验"})
_IMBALANCE_SIGNAL_CHECKS = frozenset({"不平"})
_REJECTION_CHECKS = frozenset({"不得"})
_IS_BALANCED_CHECK = "isbalanced"
_STRONG_NO_VOUCHER_TOKENS = (
    "信息不足",
    "需要补充",
    "请补充",
    "待确认",
    "无法生成",
    "不能生成",
    "不可生成",
    "不得生成",
    "无法输出",
    "不能输出",
    "insufficient information",
    "not enough information",
    "missing information",
    "cannot generate",
)
_WEAK_NO_VOUCHER_TOKENS = ("缺少",)
_NO_VOUCHER_TOKENS = _STRONG_NO_VOUCHER_TOKENS + _WEAK_NO_VOUCHER_TOKENS


def _normalize_text(text: str) -> str:
    return text.replace(",", "").lower()


def _is_no_voucher_response(text: str) -> bool:
    """回答明确没有生成凭证时，借贷类 check 视为满足边界处理。"""
    norm = _normalize_text(text)
    if any(t in norm for t in _STRONG_NO_VOUCHER_TOKENS):
        return True
    return any(t in norm for t in _WEAK_NO_VOUCHER_TOKENS)


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


def _check_keyword(text: str, check: str) -> bool:
    return _normalize_text(check) in _normalize_text(text)


def _check_no_voucher_special(predicted: str, check: str) -> bool | None:
    """仅在无凭证回答时放过借贷专项；其他情况全部回退 keyword。"""
    norm_check = (check or "").strip().lower()
    if not _is_no_voucher_response(predicted):
        return None
    if (
        check in _BALANCE_VERIFY_CHECKS
        or check in _IMBALANCE_SIGNAL_CHECKS
        or check in _REJECTION_CHECKS
        or norm_check == _IS_BALANCED_CHECK
    ):
        return True

    return None


def _match_check(
    text: str,
    check: str,
    aliases: dict[str, list[str]],
) -> tuple[bool, str | None]:
    """返回 (是否通过, 命中的别名或 None)。"""
    special = _check_no_voucher_special(text, check)
    if special is not None:
        return special, None
    if _check_keyword(text, check):
        return True, None
    for alias in _aliases_for(check, aliases):
        if _check_keyword(text, alias):
            return True, alias
    return False, None


def _check_expected(
    text: str,
    check: str,
    aliases: dict[str, list[str]],
    item: dict,
) -> bool:
    passed, _ = _match_check(text, check, aliases)
    return passed


def _classify_failure_type(
    predicted: str,
    missed_checks: list[str],
    aliases: dict[str, list[str]],
) -> tuple[str, str]:
    """Project-specific failure typing for code_diagnosis (via diagnostics)."""
    if _is_no_voucher_response(predicted):
        for check in missed_checks:
            if "会计凭证" in str(check):
                return (
                    "output_format_error",
                    "信息充分时应输出 ## 会计凭证，不得以澄清问题代替凭证。",
                )
    for check in missed_checks:
        key = (check or "").strip().lower()
        for alias in _aliases_for(check, aliases):
            if _check_keyword(predicted, alias) and not _check_keyword(predicted, check):
                return (
                    "scorer_alias_gap",
                    f"输出须覆盖评分关键词 «{check}»（可含已配置别名）。",
                )
    if missed_checks:
        missed_s = ", ".join(str(c) for c in missed_checks[:5])
        return (
            "missing_business_rule",
            f"根据代码与场景补充业务规则，确保输出包含: {missed_s}。",
        )
    return "unknown", ""


def score(predicted: str, item: dict, global_aliases: dict[str, Any] | None) -> dict:
    # 与 skillopt_loop.scoring.score_rollout_result 对齐；非凭证回答只放过借贷专项。
    checks = list(item.get("expected_checks") or [])
    aliases = _merge_aliases(global_aliases, item.get("check_aliases"))

    passed_checks: list[str] = []
    missed_checks: list[str] = []
    alias_hits: dict[str, list[str]] = {}
    for check in checks:
        passed, matched_alias = _match_check(predicted, check, aliases)
        if passed:
            passed_checks.append(check)
            if matched_alias:
                alias_hits.setdefault(check, []).append(matched_alias)
        else:
            missed_checks.append(check)

    total = len(checks) if checks else 1
    passed = len(passed_checks)
    soft = passed / total
    hard = 1 if soft == 1.0 else 0

    precision = passed / max(len(predicted.split()), 1)
    recall = soft
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )

    diagnostics: dict[str, Any] = {
        "mode": "keyword",
        "no_voucher_response": _is_no_voucher_response(predicted),
        **({"alias_hits": alias_hits} if alias_hits else {}),
    }
    if hard == 0:
        failure_type, suggested_rule = _classify_failure_type(
            predicted, missed_checks, aliases,
        )
        diagnostics["failure_type"] = failure_type
        if suggested_rule:
            diagnostics["suggested_rule"] = suggested_rule

    return {
        "hard": hard,
        "soft": round(soft, 3),
        "passed_checks": passed_checks,
        "missed_checks": missed_checks,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "justification": f"keyword checks {passed}/{total}",
        "diagnostics": diagnostics,
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
