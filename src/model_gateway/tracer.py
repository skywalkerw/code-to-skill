"""调用追踪。

记录每次模型/Agent 调用的 trace、token usage 和 cost。
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime

from .types import InteractionRequest, InteractionResponse

# 成本映射（USD / 1M tokens）
# 按 §12.4 离线部署策略，价格表可离线更新
_DEFAULT_PRICES: dict[str, dict[str, float]] = {
    "deepseek-v4-pro": {"prompt": 2.0, "completion": 8.0},
    "default": {"prompt": 1.0, "completion": 4.0},
}


class Tracer:
    """记录 trace、token usage 和 cost。"""

    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

        self._trace_path = os.path.join(output_dir, "traces.jsonl")
        self._token_path = os.path.join(output_dir, "token_usage.jsonl")
        self._cost_path = os.path.join(output_dir, "cost_usage.jsonl")

    def record(self, request: InteractionRequest, response: InteractionResponse,
               resolved_route: dict | None = None):
        """记录一次调用。"""
        ts = datetime.utcnow().isoformat()

        # trace
        trace = {
            "ts": ts,
            "request": request.model_dump(),
            "response": response.model_dump(),
            "resolved_route": resolved_route,
        }
        self._append_jsonl(self._trace_path, trace)

        # token usage
        usage = response.usage.copy()
        usage["ts"] = ts
        usage["request_id"] = response.request_id
        usage["backend_id"] = response.backend_id
        self._append_jsonl(self._token_path, usage)

        # cost
        cost = self._estimate_cost(response)
        cost["ts"] = ts
        cost["request_id"] = response.request_id
        cost["backend_id"] = response.backend_id
        self._append_jsonl(self._cost_path, cost)

    @staticmethod
    def _estimate_cost(response: InteractionResponse) -> dict:
        model = getattr(response, "model", "default")
        prices = _DEFAULT_PRICES.get(model, _DEFAULT_PRICES["default"])
        usage = response.usage
        prompt_cost = usage.get("prompt_tokens", 0) / 1_000_000 * prices["prompt"]
        completion_cost = usage.get("completion_tokens", 0) / 1_000_000 * prices["completion"]
        return {
            "prompt_cost_usd": round(prompt_cost, 6),
            "completion_cost_usd": round(completion_cost, 6),
            "total_cost_usd": round(prompt_cost + completion_cost, 6),
            "estimation_method": "token_count",
        }

    @staticmethod
    def _append_jsonl(path: str, record: dict):
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
