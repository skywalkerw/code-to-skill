#!/usr/bin/env python3
"""批量规范化 benchmark items.json 中的 context_refs 路径。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent / "sources" / "repos" / "fineract"

# 默认处理完整集；fineract-full / fineract-fast 同步更新
BENCHMARK_DIRS = ("fineract", "fineract-full", "fineract-fast")


def _load_normalizer():
    src = ROOT.parent.parent / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from code_to_skill.skillopt_loop.code_evidence import normalize_context_ref
    return normalize_context_ref


def normalize_items_file(path: Path, normalize_fn) -> int:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    changed = 0
    repo = str(REPO_ROOT) if REPO_ROOT.is_dir() else ""
    for item in data.get("items", []):
        refs = list(item.get("context_refs") or [])
        if not refs:
            continue
        new_refs = [normalize_fn(r, repo_root=repo) for r in refs]
        if new_refs != refs:
            item["context_refs"] = new_refs
            changed += len(refs)

    if changed:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
    return changed


def main() -> None:
    normalize_fn = _load_normalizer()
    if not REPO_ROOT.is_dir():
        print(f"warning: repo not found at {REPO_ROOT}, using path rules only")

    total = 0
    for bench in BENCHMARK_DIRS:
        bench_dir = ROOT / bench
        if not bench_dir.is_dir():
            continue
        for items_path in sorted(bench_dir.glob("*/items.json")):
            n = normalize_items_file(items_path, normalize_fn)
            total += n
            print(f"{items_path.relative_to(ROOT)}: {n} ref(s) updated")

    print(f"done, {total} ref(s) normalized")


if __name__ == "__main__":
    main()
