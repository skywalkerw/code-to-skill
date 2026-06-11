"""Benchmark train/selection/test split 加载与解析。"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ResolvedBenchmarkSplits:
    train: list[dict]
    selection: list[dict]
    test: list[dict]
    use_explicit_splits: bool
    source: str  # "explicit_files" | "train_only"


@dataclass
class BenchmarkSplits:
    train: list[dict]
    selection: list[dict]
    test: list[dict]

    @property
    def has_explicit_splits(self) -> bool:
        return bool(self.selection or self.test)

    @classmethod
    def from_dir(cls, path: str) -> "BenchmarkSplits":
        root = Path(path).resolve()
        benchmark_dir = str(root)
        return cls(
            train=cls._load_split(root / "train" / "items.json", benchmark_dir),
            selection=cls._load_split(root / "selection" / "items.json", benchmark_dir),
            test=cls._load_split(root / "test" / "items.json", benchmark_dir),
        )

    @staticmethod
    def _load_split(file_path: Path, benchmark_dir: str = "") -> list[dict]:
        if not file_path.exists():
            return []
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("items", [])
        # python_script scorer 解析 ../score_expected_checks.py 等相对路径时依赖此字段。
        if benchmark_dir:
            for item in items:
                item["_benchmark_dir"] = benchmark_dir
        return items

    def resolve(self) -> ResolvedBenchmarkSplits:
        """使用 benchmark 目录中的显式 split 文件；无 selection/test 时仅 train。"""
        return ResolvedBenchmarkSplits(
            train=self.train,
            selection=self.selection,
            test=self.test,
            use_explicit_splits=self.has_explicit_splits,
            source="explicit_files" if self.has_explicit_splits else "train_only",
        )

    def validate_splits(self) -> list[str]:
        warnings: list[str] = []

        def _ids(items: list[dict], label: str) -> set[str]:
            ids: set[str] = set()
            for item in items:
                iid = item.get("id")
                if not iid:
                    warnings.append(f"{label}: item missing 'id'")
                    continue
                if iid in ids:
                    warnings.append(f"{label}: duplicate id '{iid}'")
                ids.add(iid)
            return ids

        train_ids = _ids(self.train, "train")
        sel_ids = _ids(self.selection, "selection")
        test_ids = _ids(self.test, "test")

        for name, overlap in (
            ("train/selection", train_ids & sel_ids),
            ("train/test", train_ids & test_ids),
            ("selection/test", sel_ids & test_ids),
        ):
            if overlap:
                warnings.append(f"{name} overlap: {sorted(overlap)}")

        return warnings

    def log_validation(self) -> None:
        for msg in self.validate_splits():
            logger.warning("[BenchmarkSplits] %s", msg)
