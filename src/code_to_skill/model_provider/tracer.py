"""调用追踪。

trace 模式开启时，将每次 LLM 调用的完整输入（messages）和输出（content）写入 JSONL。
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

from code_to_skill.time_utils import local_timestamp

from .types import InteractionRequest, InteractionResponse

# 成本映射（USD / 1M tokens）
_DEFAULT_PRICES: dict[str, dict[str, float]] = {
    "deepseek-v4-pro": {"prompt": 2.0, "completion": 8.0},
    "default": {"prompt": 1.0, "completion": 4.0},
}

# 脱敏模式
_SECRET_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "sk-***REDACTED***"),
    (re.compile(r"Bearer\s+[A-Za-z0-9._\-]+", re.I), "Bearer ***REDACTED***"),
    (re.compile(r"(api[_-]?key\s*[:=]\s*)['\"]?[A-Za-z0-9._\-]{8,}", re.I), r"\1***REDACTED***"),
]


def _redact_text(text: str) -> tuple[str, bool]:
    """脱敏文本中的密钥，返回 (redacted_text, was_redacted)。"""
    redacted = text
    changed = False
    for pattern, replacement in _SECRET_PATTERNS:
        new_text, n = pattern.subn(replacement, redacted)
        if n:
            redacted = new_text
            changed = True
    return redacted, changed


def _redact_obj(obj: Any) -> tuple[Any, bool]:
    """递归脱敏 dict/list/str。"""
    if isinstance(obj, str):
        return _redact_text(obj)
    if isinstance(obj, list):
        redacted_list = []
        any_changed = False
        for item in obj:
            r, c = _redact_obj(item)
            redacted_list.append(r)
            any_changed = any_changed or c
        return redacted_list, any_changed
    if isinstance(obj, dict):
        redacted_dict = {}
        any_changed = False
        for k, v in obj.items():
            r, c = _redact_obj(v)
            redacted_dict[k] = r
            any_changed = any_changed or c
        return redacted_dict, any_changed
    return obj, False


class Tracer:
    """将完整 request/response 写入 traces.jsonl，并汇总 token/cost。"""

    def __init__(self, output_dir: str, redact_secrets: bool = True):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        os.makedirs(os.path.join(output_dir, "calls"), exist_ok=True)

        self.redact_secrets = redact_secrets
        self._call_index = 0

        self._trace_path = os.path.join(output_dir, "traces.jsonl")
        self._token_path = os.path.join(output_dir, "token_usage.jsonl")
        self._cost_path = os.path.join(output_dir, "cost_usage.jsonl")

    def record(
        self,
        request: InteractionRequest,
        response: InteractionResponse,
        backend_id: str = "",
        resolved_route: dict | None = None,
    ):
        """记录一次调用的完整输入输出。"""
        self._call_index += 1
        ts = local_timestamp()

        req_dump = request.model_dump()
        resp_dump = response.model_dump()
        redactions: list[str] = []

        if self.redact_secrets:
            req_dump, req_changed = _redact_obj(req_dump)
            resp_dump, resp_changed = _redact_obj(resp_dump)
            if req_changed:
                redactions.append("request")
            if resp_changed:
                redactions.append("response")

        trace = {
            "schema_version": "1.0",
            "call_index": self._call_index,
            "created_at": ts,
            "backend_id": backend_id or getattr(response, "backend_id", ""),
            "request": req_dump,
            "response": resp_dump,
            "resolved_route": resolved_route,
            "retries": [],
            "redactions": redactions,
        }
        self._append_jsonl(self._trace_path, trace)

        # 单条调用文件，便于直接打开查看完整内容
        call_path = os.path.join(self.output_dir, "calls", f"{self._call_index:04d}.json")
        with open(call_path, "w", encoding="utf-8") as f:
            json.dump(trace, f, ensure_ascii=False, indent=2)

        usage = response.usage.copy()
        usage["ts"] = ts
        usage["call_index"] = self._call_index
        usage["request_id"] = response.request_id
        usage["backend_id"] = backend_id or response.backend_id
        usage["role"] = request.role
        usage["stage"] = request.stage
        self._append_jsonl(self._token_path, usage)

        cost = self._estimate_cost(response)
        cost["ts"] = ts
        cost["call_index"] = self._call_index
        cost["request_id"] = response.request_id
        cost["backend_id"] = backend_id or response.backend_id
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


class _NoopTraceManager:
    """trace 关闭时的空实现。"""

    enabled = False

    def record(self, *args, **kwargs):
        pass


_noop = _NoopTraceManager()
_manager: TraceManager | None = None


class TraceManager:
    """全局 trace 管理器。"""

    def __init__(self, output_dir: str, enabled: bool = True, redact_secrets: bool = True):
        self.enabled = enabled
        self._tracer = Tracer(output_dir, redact_secrets=redact_secrets) if enabled else None

    def record(
        self,
        request: InteractionRequest,
        response: InteractionResponse,
        backend_id: str = "",
        resolved_route: dict | None = None,
    ):
        if self.enabled and self._tracer:
            self._tracer.record(request, response, backend_id, resolved_route)


def configure_trace(
    output_dir: str,
    enabled: bool = True,
    redact_secrets: bool = True,
) -> TraceManager:
    """配置全局 trace。在流水线开始前调用一次。"""
    global _manager
    if enabled:
        _manager = TraceManager(output_dir, enabled=True, redact_secrets=redact_secrets)
    else:
        _manager = None
    return get_trace_manager()


def get_trace_manager() -> TraceManager | _NoopTraceManager:
    """获取当前 trace 管理器。"""
    return _manager if _manager is not None else _noop


def record_interaction(
    request: InteractionRequest,
    response: InteractionResponse,
    backend_id: str = "",
    resolved_route: dict | None = None,
):
    """记录一次 LLM 交互（backend 层调用）。"""
    get_trace_manager().record(request, response, backend_id, resolved_route)


def is_trace_enabled() -> bool:
    return get_trace_manager().enabled
