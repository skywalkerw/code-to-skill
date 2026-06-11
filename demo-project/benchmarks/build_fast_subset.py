#!/usr/bin/env python3
"""从 fineract-full 生成 fineract-fast 子集。"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "fineract-full"
TARGET = ROOT / "fineract-fast"

SUBSETS = {
    "train": [
        "jv_purchase_001",
        "jv_loan_disburse_001",
        "jv_repayment_principal_001",
        "jv_repayment_interest_001",
        "jv_savings_interest_001",
    ],
    "selection": [
        "jv_incomplete_001",
        "jv_constraint_001",
        "jv_sel_disburse_001",
        "jv_sel_repay_split_001",
        "jv_sel_purchase_001",
        "jv_sel_interest_repay_001",
    ],
    "test": [
        "jv_purchase_002",
        "jv_savings_accrual_001",
        "jv_transfer_001",
    ],
}


def main() -> None:
    if not SOURCE.is_dir():
        raise SystemExit(f"source not found: {SOURCE}")

    for split, ids in SUBSETS.items():
        src = SOURCE / split / "items.json"
        with open(src, encoding="utf-8") as f:
            data = json.load(f)
        by_id = {item["id"]: item for item in data["items"]}
        missing = [i for i in ids if i not in by_id]
        if missing:
            raise SystemExit(f"missing ids in {split}: {missing}")

        out_dir = TARGET / split
        out_dir.mkdir(parents=True, exist_ok=True)
        out = {
            "schema_version": data.get("schema_version", "1.0"),
            "items": [by_id[i] for i in ids],
        }
        out_path = out_dir / "items.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"{split}: {len(out['items'])} -> {out_path}")


if __name__ == "__main__":
    main()
