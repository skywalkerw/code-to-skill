"""Selection Score 缓存。

对齐 external/SkillOpt 的 selection cache 设计。

缓存策略：
- 用语义 hash 索引（`compute_semantic_hash`），同一 Skill 内容不重复 eval
- 跨 step 复用，跨 run 不复用
- 满 1000 条时淘汰最旧条目
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# 缓存文件最大条目数
_MAX_CACHE_ENTRIES = 1000


class SelectionCache:
    """Selection split 评分缓存。

    Usage:
        cache = SelectionCache()
        # 在 evaluate 前：
        cached = cache.get(semantic_hash)
        if cached is not None:
            score = cached["gate_score"]
        else:
            score = actual_evaluate(...)
            cache.put(semantic_hash, hard, soft, gate, epoch, step)
    """

    def __init__(self, cache_path: str | None = None):
        self._entries: dict[str, dict] = {}
        self._order: list[str] = []  # FIFO 顺序
        self._cache_path = cache_path
        self._loaded = False

    def get(self, semantic_hash: str) -> dict | None:
        """查询缓存。返回 None 表示未命中。"""
        return self._entries.get(semantic_hash)

    def put(
        self,
        semantic_hash: str,
        hard_score: float,
        soft_score: float,
        gate_score: float,
        epoch: int = 0,
        step: int = 0,
    ) -> None:
        """写入缓存。"""
        # 淘汰最旧
        while len(self._entries) >= _MAX_CACHE_ENTRIES and self._order:
            old = self._order.pop(0)
            self._entries.pop(old, None)

        self._entries[semantic_hash] = {
            "skill_semantic_hash": semantic_hash,
            "hard_score": hard_score,
            "soft_score": soft_score,
            "gate_score": gate_score,
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "epoch": epoch,
            "step": step,
        }
        if semantic_hash not in self._order:
            self._order.append(semantic_hash)

    def size(self) -> int:
        return len(self._entries)

    def stats(self) -> dict:
        """返回缓存统计。"""
        hits = sum(
            1 for h in self._order
            if self._entries.get(h, {}).get("epoch", 0) > 0
        )
        return {
            "size": len(self._entries),
            "max": _MAX_CACHE_ENTRIES,
        }

    # ── 持久化（可选）────────────────────────────────────

    def save(self) -> None:
        """将缓存写入磁盘。"""
        if not self._cache_path:
            return
        data = {
            "schema_version": "1.0",
            "entries": self._entries,
        }
        Path(self._cache_path).parent.mkdir(parents=True, exist_ok=True)
        with open(self._cache_path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.debug("SelectionCache saved: %d entries", len(self._entries))

    def load(self) -> None:
        """从磁盘加载缓存。"""
        if self._loaded or not self._cache_path:
            return
        path = Path(self._cache_path)
        if not path.exists():
            self._loaded = True
            return
        try:
            with open(path) as f:
                data = json.load(f)
            self._entries = data.get("entries", {})
            self._order = list(self._entries.keys())
            self._loaded = True
            logger.debug("SelectionCache loaded: %d entries", len(self._entries))
        except Exception as e:
            logger.warning("Failed to load SelectionCache: %s", e)
            self._loaded = True
