"""M3 产物引导 benchmark / initial_skill（Phase 2）。"""
from __future__ import annotations

import json
import os

from code_to_skill.atom_extractor.merger import generate_benchmark_seeds
from code_to_skill.atom_extractor.types import SkillAtom
from code_to_skill.skillopt_loop.benchmark_splits import BenchmarkSplits

AUTO_RULES_HEADER = "### Auto-suggested rules"


def load_m3_from_run(run_root: str) -> dict | None:
    """从 run 目录加载 M3 产物（merged_atoms + benchmark_seeds）。"""
    atoms_dir = os.path.join(run_root, "atoms")
    merged_path = os.path.join(atoms_dir, "merged_atoms.jsonl")
    seeds_path = os.path.join(atoms_dir, "benchmark_seeds.jsonl")
    if not os.path.isfile(merged_path):
        return None

    merged_atoms: list[SkillAtom] = []
    with open(merged_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                merged_atoms.append(SkillAtom.model_validate_json(line))

    seeds: list[dict] = []
    if os.path.isfile(seeds_path):
        with open(seeds_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    seeds.append(json.loads(line))
    if not seeds and merged_atoms:
        seeds = generate_benchmark_seeds(merged_atoms)

    return {"merged_atoms": merged_atoms, "benchmark_seeds": seeds}


def high_confidence_train_items(
    m3: dict,
    *,
    min_confidence: float = 0.8,
    accepted_statuses: tuple[str, ...] = ("accepted",),
) -> list[dict]:
    """从高置信 atom 生成 train items（对齐 items.json schema）。"""
    atoms = [
        a for a in m3.get("merged_atoms", [])
        if a.confidence >= min_confidence and a.status in accepted_statuses
    ]
    return generate_benchmark_seeds(atoms)


def merge_train_items(existing: list[dict], new_items: list[dict]) -> list[dict]:
    """按 id 去重追加 train items。"""
    seen = {item.get("id") for item in existing if item.get("id")}
    merged = list(existing)
    for item in new_items:
        iid = item.get("id")
        if not iid or iid in seen:
            continue
        merged.append(item)
        seen.add(iid)
    return merged


def apply_benchmark_bootstrap(
    splits: BenchmarkSplits,
    m3: dict,
    *,
    merge: bool = False,
    min_confidence: float = 0.8,
) -> BenchmarkSplits:
    """将 M3 高置信种子并入 benchmark splits。"""
    seeds = high_confidence_train_items(m3, min_confidence=min_confidence)
    if not seeds:
        return splits

    if splits.train and not merge:
        return splits

    train = merge_train_items(splits.train, seeds) if merge else seeds
    return BenchmarkSplits(train=train, selection=splits.selection, test=splits.test)


def write_train_items(benchmark_dir: str, items: list[dict]) -> str:
    """将 train items 写回 ``benchmark/train/items.json``。"""
    root = os.path.join(benchmark_dir, "train")
    os.makedirs(root, exist_ok=True)
    out_path = os.path.join(root, "items.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"items": items}, f, indent=2, ensure_ascii=False)
    return out_path


def append_atom_rules_to_skill(
    skill: str,
    m3: dict,
    *,
    min_confidence: float = 0.8,
) -> str:
    """将高置信 atom claims 追加为 ``### Auto-suggested rules`` 附录。"""
    atoms = [
        a for a in m3.get("merged_atoms", [])
        if a.confidence >= min_confidence and a.status == "accepted" and a.claim.strip()
    ]
    if not atoms:
        return skill

    if AUTO_RULES_HEADER in skill:
        base = skill.split(AUTO_RULES_HEADER)[0].rstrip()
    else:
        base = skill.rstrip()

    lines = [
        base,
        "",
        AUTO_RULES_HEADER,
        "",
        "_From M3 atoms (review before gate)._",
        "",
    ]
    for atom in atoms[:30]:
        lines.append(f"- {atom.claim.strip()}")
    return "\n".join(lines) + "\n"
