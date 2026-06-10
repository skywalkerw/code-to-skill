"""Design 08 — 自进化 run 产物校验。"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def validate_self_evolution_run(opt_dir: str | Path) -> dict[str, Any]:
    """检查 optimization 目录下 Design 08 产物是否齐全、格式是否合理。"""
    root = Path(opt_dir)
    checks: list[dict[str, Any]] = []

    def _add(name: str, ok: bool, detail: str = "", path: str = "") -> None:
        checks.append({
            "name": name,
            "ok": ok,
            "detail": detail,
            "path": path,
        })

    cfg_path = root / "config.json"
    if cfg_path.is_file():
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
        se = cfg.get("self_evolution") or {}
        _add(
            "config.self_evolution",
            bool(se.get("enabled")),
            f"mode={se.get('mode', '?')}",
            str(cfg_path),
        )
    else:
        _add("config.json", False, "missing", str(cfg_path))

    traces = root / "trace_pool" / "traces.jsonl"
    trace_count = 0
    if traces.is_file():
        with open(traces, encoding="utf-8") as f:
            trace_count = sum(1 for line in f if line.strip())
    _add(
        "trace_pool.traces",
        trace_count > 0,
        f"{trace_count} traces",
        str(traces),
    )

    clusters = root / "trace_pool" / "clusters.json"
    cluster_n = 0
    if clusters.is_file():
        with open(clusters, encoding="utf-8") as f:
            data = json.load(f)
        cluster_n = len((data or {}).get("clusters") or [])
    _add(
        "trace_pool.clusters",
        clusters.is_file(),
        f"{cluster_n} clusters",
        str(clusters),
    )

    prop_dir = root / "proposals"
    prop_files = list(prop_dir.glob("*.jsonl")) if prop_dir.is_dir() else []
    _add(
        "proposals",
        len(prop_files) >= 2,
        f"{len(prop_files)} jsonl files",
        str(prop_dir),
    )

    rej = root / "rejected_edit_buffer.jsonl"
    rej_n = 0
    if rej.is_file():
        with open(rej, encoding="utf-8") as f:
            rej_n = sum(1 for line in f if line.strip())
    _add(
        "rejected_edit_buffer",
        True,
        f"{rej_n} entries (optional)",
        str(rej),
    )

    attr = root / "rule_attribution.json"
    rule_n = 0
    if attr.is_file():
        with open(attr, encoding="utf-8") as f:
            raw = json.load(f)
        rules = raw.get("rules") if isinstance(raw, dict) else {}
        rule_n = len(rules or {})
    _add(
        "rule_attribution",
        attr.is_file(),
        f"{rule_n} rules",
        str(attr),
    )

    frontier = root / "frontier" / "frontier.json"
    frontier_n = 0
    if frontier.is_file():
        with open(frontier, encoding="utf-8") as f:
            raw = json.load(f)
        frontier_n = len((raw or {}).get("entries") or [])
    _add(
        "frontier",
        True,
        f"{frontier_n} entries (optional)",
        str(frontier),
    )

    history = root / "history.json"
    gate_meta = False
    history_rows: list = []
    if history.is_file():
        with open(history, encoding="utf-8") as f:
            raw = json.load(f)
        history_rows = raw if isinstance(raw, list) else []
        if history_rows and isinstance(history_rows[-1], dict):
            last = history_rows[-1]
            gate_meta = "gate_reason" in last and "candidate_hash" in last
    history_required = bool(history_rows)
    _add(
        "history.gate_metadata",
        gate_meta if history_required else True,
        (
            "gate_reason + candidate_hash on last step"
            if history_required
            else "no gate steps (all skipped — OK)"
        ),
        str(history),
    )

    required = [
        c for c in checks
        if c["name"] not in ("rejected_edit_buffer", "frontier", "rule_attribution")
    ]
    passed = all(c["ok"] for c in required)
    return {
        "passed": passed,
        "checks": checks,
        "optimization_dir": str(root),
    }
